"""Appliance part of the GENETIC genome: allowed starts, layout and decoding.

Pure functions of the appliances and the run's slot grid. The algorithm builds and decodes its
genome with them; the parameter preparation checks with the same rules that every appliance of a
run can be scheduled.
"""

import math
from collections import defaultdict
from dataclasses import dataclass, field
from functools import lru_cache
from typing import Any, Optional

from akkudoktoreos.optimization.genetic.devices.genetichomeappliance import (
    GeneticHomeAppliance,
    GeneticScheduleMode,
)


@dataclass
class ApplianceGeneSlot:
    """One appliance start gene in the genome.

    The gene value is an **index into ``allowed_start_slots``**, not an absolute
    slot. This guarantees every gene value maps to a genuinely valid start and
    keeps all allowed starts equally reachable by mutation/crossover.
    """

    gene_index: int
    appliance_index: int
    device_id: str
    run_index: int
    # Local calendar date of the run for DAILY appliances; None for ONCE.
    run_date: Optional[Any]
    allowed_start_slots: list[int]
    cycle_index: int = 0
    deadline_relaxed: bool = False


@dataclass
class ApplianceGeneLayout:
    """Ordered descriptor of the appliance part of the genome.

    Every genome-building step (create/split/merge/mutate/decode) consumes only
    this descriptor, so the appliance gene block can vary in length with the
    number of devices and DAILY run days without any hard-coded gene positions.
    """

    genes: list[ApplianceGeneSlot] = field(default_factory=list)

    @property
    def n_genes(self) -> int:
        """Number of appliance start genes."""
        return len(self.genes)

    def signature(self) -> tuple:
        """Stable identity of the layout for start-solution compatibility.

        Two layouts with the same length can still describe different schedules;
        the signature captures device, run date and the allowed-start list so a
        cached start solution built for a different layout is not silently
        reused.
        """
        return tuple(
            (gene.device_id, str(gene.run_date), gene.cycle_index, tuple(gene.allowed_start_slots))
            for gene in self.genes
        )


def _run_date(appliance: GeneticHomeAppliance, slot0_datetime: Any, slot: int) -> Any:
    """Local date of a run starting in ``slot``, the day a DAILY cycle counts for."""
    return slot0_datetime.add(seconds=slot * appliance.slot_interval_seconds).date()


def build_appliance_layout(
    appliances: list[GeneticHomeAppliance],
    slot0_datetime: Any,
    *,
    start_slot: int,
    horizon_end_slot: int,
    control_end_slot: int,
    slot_duration_h: float,
    order_cache: dict[int, list[int]],
) -> ApplianceGeneLayout:
    """Keep each cycle's windows, completed cycles and local-day identity.

    Args:
        appliances: The appliances of the run, with any run in progress pinned.
        slot0_datetime: Date-time of slot 0, the run start.
        start_slot: First slot an appliance may start in.
        horizon_end_slot: Exclusive end of the slots an appliance run must end in.
        control_end_slot: Exclusive control end of the run.
        slot_duration_h: Length of a slot in hours.
        order_cache: Feasible cycle order per appliance index; filled for the decoding.

    Returns:
        The appliance genes of the genome.

    Raises:
        ValueError: An appliance cannot be scheduled within its windows, deadline, minimum gaps
            and the control horizon.
    """
    genes: list[ApplianceGeneSlot] = []
    first_date = slot0_datetime.date()
    for appliance_index, appliance in enumerate(appliances):
        cycles = range(appliance.num_cycles)
        # A run in progress counts as done for its day and delays the next
        # cycle until after its end and the minimum gap.
        completed_cycles = appliance.completed_cycles_on(first_date)
        earliest_slot = max(start_slot, appliance.next_cycle_earliest_slot())
        allowed_by_cycle: dict[int, list[int]] = {}
        relaxed_by_cycle: dict[int, bool] = {}
        for cycle in cycles:
            allowed_by_cycle[cycle] = appliance.allowed_start_slots(
                slot0_datetime=slot0_datetime,
                earliest_slot=earliest_slot,
                horizon_end_slot=horizon_end_slot,
                cycle_index=cycle,
            )
            relaxed_by_cycle[cycle] = appliance.deadline_relaxed
        if appliance.schedule_mode == GeneticScheduleMode.ONCE:
            groups = [
                (None, cycle, allowed_by_cycle[cycle])
                for cycle in cycles
                if cycle >= completed_cycles
            ]
        else:
            by_date: dict[Any, dict[int, list[int]]] = {}
            for cycle, allowed in allowed_by_cycle.items():
                for slot in allowed:
                    date = _run_date(appliance, slot0_datetime, slot)
                    if date == first_date and cycle < completed_cycles:
                        continue
                    by_date.setdefault(date, {}).setdefault(cycle, []).append(slot)
            # Without any start the appliance cannot be scheduled if its windows and earliest
            # start are open for a cycle it still owes, but too short for the full run. A
            # horizon in which they are not open at all has nothing to plan.
            if not by_date and any(
                not (
                    _run_date(appliance, slot0_datetime, slot) == first_date
                    and cycle < completed_cycles
                )
                for cycle in cycles
                for slot in appliance.open_start_slots(
                    slot0_datetime=slot0_datetime,
                    earliest_slot=earliest_slot,
                    horizon_end_slot=horizon_end_slot,
                    cycle_index=cycle,
                )
            ):
                raise ValueError(
                    f"Home appliance '{appliance.device_id}' has no valid start within its windows, deadline and control horizon."
                )
            groups = [
                (date, cycle, allowed)
                for date, cycle_slots in sorted(by_date.items())
                for cycle, allowed in sorted(cycle_slots.items())
            ]
            # A partially elapsed first day may have no feasible remaining run;
            # later complete planning days must retain all configured cycles.
            for date, cycle_slots in by_date.items():
                expected = {
                    cycle for cycle in cycles if date != first_date or cycle >= completed_cycles
                }
                if set(cycle_slots) != expected:
                    raise ValueError(
                        f"Home appliance '{appliance.device_id}' has no complete cycle schedule on {date}."
                    )
        for run_index, (date, cycle, allowed) in enumerate(groups):
            if not allowed:
                raise ValueError(
                    f"Home appliance '{appliance.device_id}' cycle {cycle} has no valid start within its windows, deadline and control horizon."
                )
            genes.append(
                ApplianceGeneSlot(
                    gene_index=len(genes),
                    appliance_index=appliance_index,
                    device_id=appliance.device_id,
                    run_index=run_index,
                    run_date=date,
                    allowed_start_slots=allowed,
                    cycle_index=cycle,
                    deadline_relaxed=relaxed_by_cycle[cycle],
                )
            )
    layout = ApplianceGeneLayout(genes)
    # Detect impossible cross-cycle gaps before running a population search.
    decode_appliance_starts(
        layout,
        appliances,
        [0] * layout.n_genes,
        start_slot=start_slot,
        control_end_slot=control_end_slot,
        slot_duration_h=slot_duration_h,
        order_cache=order_cache,
    )
    return layout


def decode_appliance_starts(
    layout: ApplianceGeneLayout,
    appliances: list[GeneticHomeAppliance],
    appliance_gene_values: list[int],
    *,
    start_slot: int,
    control_end_slot: int,
    slot_duration_h: float,
    order_cache: dict[int, list[int]],
) -> dict[int, list[int]]:
    """Repair requested starts jointly, preserving cycle windows and minimum gaps.

    Backward latest-feasible bounds and a forward nearest-choice pass prevent
    an unlucky late first gene from making otherwise feasible cycles invalid.
    The canonical gene values are updated to describe the executed schedule.

    Args:
        layout: The appliance genes of the genome.
        appliances: The appliances the genes refer to by index.
        appliance_gene_values: The appliance genes; updated to the executed schedule.
        start_slot: First slot an appliance may start in.
        control_end_slot: Exclusive control end of the run.
        slot_duration_h: Length of a slot in hours.
        order_cache: Feasible cycle order per appliance index, found once per run.

    Returns:
        The start slots of each appliance, by appliance index.

    Raises:
        ValueError: An appliance has no feasible schedule.
    """
    starts_per_appliance: dict[int, list[int]] = defaultdict(list)
    grouped: dict[int, list[tuple[int, ApplianceGeneSlot]]] = defaultdict(list)
    for position, gene in enumerate(layout.genes):
        grouped[gene.appliance_index].append((position, gene))
    for appliance_index, entries in grouped.items():
        appliance = appliances[appliance_index]
        separation = appliance.run_slots + math.ceil(appliance.min_cycle_gap_h / slot_duration_h)
        by_position = dict(entries)

        def requested_start(entry: tuple[int, ApplianceGeneSlot]) -> int:
            position, gene = entry
            index = min(
                max(int(appliance_gene_values[position]), 0), len(gene.allowed_start_slots) - 1
            )
            return gene.allowed_start_slots[0 if gene.deadline_relaxed else index]

        def latest_bounds(order: list[tuple[int, ApplianceGeneSlot]]) -> Optional[list[int]]:
            bounds: list[int] = []
            limit = control_end_slot
            for _, gene in reversed(order):
                candidates = [slot for slot in gene.allowed_start_slots if slot <= limit]
                if not candidates:
                    return None
                last = candidates[-1]
                bounds.append(last)
                limit = last - separation
            return list(reversed(bounds))

        # Cycle numbers identify masks; they do not impose temporal order.
        # Keep every feasible order represented by a candidate genome.
        entries.sort(key=lambda entry: (requested_start(entry), entry[0]))
        latest = latest_bounds(entries)
        if latest is None:
            if appliance_index not in order_cache:

                @lru_cache(maxsize=None)
                def feasible_order(
                    remaining: tuple[int, ...], earliest: int
                ) -> Optional[tuple[int, ...]]:
                    if not remaining:
                        return ()
                    choices = []
                    for position in remaining:
                        allowed = [
                            slot
                            for slot in by_position[position].allowed_start_slots
                            if slot >= earliest
                        ]
                        if not allowed:
                            return None
                        choices.append((allowed[0], allowed[-1], position))
                    # For a fixed order the earliest start dominates all later
                    # starts for feasibility. Search permutations only once
                    # per run, memoizing impossible remaining-cycle states.
                    for first, _, position in sorted(choices):
                        rest = feasible_order(
                            tuple(index for index in remaining if index != position),
                            first + separation,
                        )
                        if rest is not None:
                            return (position, *rest)
                    return None

                order = feasible_order(tuple(sorted(by_position)), start_slot)
                if order is None:
                    raise ValueError(
                        f"Home appliance '{appliance.device_id}' cycles cannot fit their windows and minimum gaps."
                    )
                order_cache[appliance_index] = list(order)
            entries = [
                (position, by_position[position]) for position in order_cache[appliance_index]
            ]
            latest = latest_bounds(entries)
            if latest is None:
                raise ValueError(
                    f"Home appliance '{appliance.device_id}' has no feasible cycle schedule."
                )
        earliest = start_slot
        for (position, gene), last in zip(entries, latest):
            allowed = gene.allowed_start_slots
            requested_index = min(max(int(appliance_gene_values[position]), 0), len(allowed) - 1)
            requested = allowed[requested_index]
            candidates = [slot for slot in allowed if earliest <= slot <= last]
            if not candidates:
                raise ValueError(f"Home appliance '{gene.device_id}' has no feasible cycle start.")
            chosen = (
                candidates[0]
                if gene.deadline_relaxed
                else min(candidates, key=lambda slot: (abs(slot - requested), slot))
            )
            starts_per_appliance[appliance_index].append(chosen)
            appliance_gene_values[position] = allowed.index(chosen)
            earliest = chosen + separation
    return starts_per_appliance
