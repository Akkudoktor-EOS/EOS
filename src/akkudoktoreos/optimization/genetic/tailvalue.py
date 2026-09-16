"""Chronological, run-local battery lookahead using the production device physics.

The Bellman recursion interpolates continuation values on a stored-energy grid.
Tail actions never enter the executable control arrays. The selected path may
be returned separately as diagnostics so users can inspect the lookahead.
"""

import numpy as np
from pydantic import PrivateAttr

from akkudoktoreos.devices.genetic.battery import Battery
from akkudoktoreos.devices.genetic.inverter import Inverter
from akkudoktoreos.optimization.genetic.terminalvalue import TailPlanSlot, TerminalValueCurve


TailAction = tuple[int, int, float, float]


def _action_name(action: TailAction) -> str:
    dc, discharge, ac_rate, export_rate = action
    if export_rate > 0:
        return "BATTERY_EXPORT"
    if ac_rate > 0:
        return "GRID_CHARGE"
    if dc and discharge:
        return "SELF_CONSUMPTION"
    if dc:
        return "PV_CHARGE_ONLY"
    if discharge:
        return "DISCHARGE_ONLY"
    return "HOLD"


def _simulate_action(
    *,
    bat: Battery,
    inv: Inverter,
    energy_wh: float,
    action: TailAction,
    price: float,
    load: float,
    pv: float,
    tariff: float,
    direct_marketing: bool,
) -> dict[str, float]:
    """Apply one tail action from one stored-energy state."""
    dc, discharge, ac_rate, export = action
    bat.soc_wh = float(energy_wh)
    bat._charged_raw_wh_per_slot.fill(0)
    bat._discharged_raw_wh_per_slot.fill(0)
    ac_enabled = inv.ac_to_dc_efficiency > 0 and (
        inv.max_ac_charge_power_w is None or inv.max_ac_charge_power_w > 0
    )
    bat.charge_array[0] = ac_rate if ac_rate > 0 and ac_enabled else dc
    bat.discharge_array[0] = discharge if export == 0 or tariff > 0 else 0
    sold, bought, losses, _ = inv.process_energy(
        pv,
        load,
        0,
        allow_battery_grid_export=direct_marketing and export > 0 and tariff > 0,
        battery_grid_export_factor=export,
    )
    ac_grid_charge_wh = 0.0
    if ac_rate > 0 and inv.ac_to_dc_efficiency > 0:
        rate = ac_rate
        if inv.max_ac_charge_power_w is not None and bat.max_charge_power_w > 0:
            rate = min(
                rate,
                inv.max_ac_charge_power_w * inv.ac_to_dc_efficiency / bat.max_charge_power_w,
            )
        bat.charge_array[0] = rate
        if rate > 0:
            stored, loss = bat.charge_energy(None, 0, charge_factor=rate)
            ac_grid_charge_wh = (stored + loss) / inv.ac_to_dc_efficiency
            bought += ac_grid_charge_wh
            losses += loss + max(ac_grid_charge_wh - stored - loss, 0.0)
    if direct_marketing and tariff < 0:
        sold = 0.0
    discharged_wh = bat.discharged_energy_wh(0)
    reward = (
        sold * tariff - bought * price - (discharged_wh * bat.levelized_cost_of_storage_kwh / 1000)
    )
    return {
        "next_state_wh": bat.soc_wh,
        "reward_euro": reward,
        "grid_export_wh": sold,
        "grid_import_wh": bought,
        "battery_charge_wh": (bat._charged_raw_wh_per_slot[0] * bat.charging_efficiency),
        "battery_discharge_wh": discharged_wh,
        "losses_wh": losses,
        "ac_grid_charge_wh": ac_grid_charge_wh,
    }


class TailValueCurve(TerminalValueCurve):
    """Value of usable AC battery energy, including the value of empty capacity.

    Neither values nor marginal values are constrained to be monotone. The empty
    state can earn money by charging at negative prices and selling later.
    """

    _trace_context: dict = PrivateAttr(default_factory=dict)

    def value(self, energy_wh: float) -> float:
        return (
            float(np.interp(energy_wh, self.energy_wh, self.value_euro)) if self.energy_wh else 0.0
        )

    def component_values(self, energy_wh: float) -> tuple[float, float]:
        """Return tail operating cash flow and continuation credit separately."""
        if not self.energy_wh:
            return 0.0, 0.0
        operating = float(np.interp(energy_wh, self.energy_wh, self.operating_value_euro))
        continuation = float(np.interp(energy_wh, self.energy_wh, self.continuation_value_euro))
        return operating, continuation

    def diagnostic_plan(self, energy_wh: float, control_horizon_hours: float) -> list[TailPlanSlot]:
        """Replay the optimal tail path for one control-end battery state."""
        context = self._trace_context
        if not context:
            return []
        bat = Battery(
            context["battery_parameters"],
            prediction_hours=1,
            slot_duration_h=context["slot_duration_h"],
        )
        inv = Inverter(
            context["inverter_parameters"],
            battery=bat,
            slot_duration_h=context["slot_duration_h"],
        )
        conversion = bat.discharging_efficiency * inv.dc_to_ac_efficiency
        state_wh = bat.min_soc_wh + (energy_wh / conversion if conversion > 0 else 0.0)
        state_wh = float(np.clip(state_wh, bat.min_soc_wh, bat.max_soc_wh))
        plan: list[TailPlanSlot] = []
        arrays = zip(
            context["prices"],
            context["load"],
            context["pv"],
            context["tariffs"],
        )
        for slot, (price, load, pv, tariff) in enumerate(arrays):
            candidates: list[tuple[float, TailAction, dict[str, float], float]] = []
            for action in context["actions"]:
                result = _simulate_action(
                    bat=bat,
                    inv=inv,
                    energy_wh=state_wh,
                    action=action,
                    price=price,
                    load=load,
                    pv=pv,
                    tariff=tariff,
                    direct_marketing=context["direct_marketing"],
                )
                remaining = float(
                    np.interp(
                        result["next_state_wh"],
                        context["states"],
                        context["future_values"][slot + 1],
                    )
                )
                candidates.append((result["reward_euro"] + remaining, action, result, remaining))
            candidates.sort(key=lambda candidate: candidate[0], reverse=True)
            chosen_value, chosen_action, chosen_result, remaining = candidates[0]
            alternative = next(
                (
                    candidate
                    for candidate in candidates[1:]
                    if _action_name(candidate[1]) != _action_name(chosen_action)
                ),
                candidates[1] if len(candidates) > 1 else candidates[0],
            )
            dc, discharge, ac_rate, export_rate = chosen_action
            start_soc = state_wh / bat.capacity_wh * 100
            state_wh = chosen_result["next_state_wh"]
            plan.append(
                TailPlanSlot(
                    slot=slot,
                    hour_from_start=control_horizon_hours + slot * context["slot_duration_h"],
                    action=_action_name(chosen_action),
                    alternative_action=_action_name(alternative[1]),
                    decision_margin_euro=max(chosen_value - alternative[0], 0.0),
                    soc_start_percentage=start_soc,
                    soc_end_percentage=state_wh / bat.capacity_wh * 100,
                    pv_wh=pv,
                    load_wh=load,
                    grid_import_wh=chosen_result["grid_import_wh"],
                    grid_export_wh=chosen_result["grid_export_wh"],
                    battery_charge_wh=chosen_result["battery_charge_wh"],
                    battery_discharge_wh=chosen_result["battery_discharge_wh"],
                    import_price_euro_per_kwh=price * 1000,
                    feed_in_tariff_euro_per_kwh=tariff * 1000,
                    slot_value_euro=chosen_result["reward_euro"],
                    remaining_value_euro=remaining,
                    ac_charge_factor=ac_rate,
                    dc_charge_allowed=dc,
                    discharge_allowed=discharge,
                    battery_grid_export_factor=export_rate,
                )
            )
        return plan


def build_tail_value_curve(
    *,
    battery: Battery,
    inverter: Inverter,
    prices_euro_per_wh: np.ndarray,
    load_wh: np.ndarray,
    pv_wh: np.ndarray,
    feed_in_euro_per_wh: np.ndarray,
    continuation: TerminalValueCurve,
    charge_rates: list[float],
    export_rates: list[float],
    direct_marketing: bool,
    grid_points: int = 101,
) -> TailValueCurve:
    """Solve the finite tail once, backwards in time, without mutating run devices.

    LCOS uses delivered DC energy, exactly as in GeneticSimulation. State grid
    endpoints include battery minimum and maximum SOC. Continuous next states are
    interpolated rather than rounded (which would invent or destroy energy).
    """
    arrays = [
        np.asarray(a, dtype=float)
        for a in (prices_euro_per_wh, load_wh, pv_wh, feed_in_euro_per_wh)
    ]
    if len({len(a) for a in arrays}) != 1 or any(not np.isfinite(a).all() for a in arrays):
        raise ValueError("Tail forecasts must have equal lengths and contain only finite values")
    bat = Battery(battery.parameters, prediction_hours=1, slot_duration_h=battery.slot_duration_h)
    bat.charge_array = np.zeros(1, dtype=float)
    inv = Inverter(inverter.parameters, battery=bat, slot_duration_h=battery.slot_duration_h)
    states = np.linspace(bat.min_soc_wh, bat.max_soc_wh, grid_points)
    usable = (states - bat.min_soc_wh) * bat.discharging_efficiency * inv.dc_to_ac_efficiency
    continuation_values = np.array([continuation.value(e) for e in usable])
    operating_values = np.zeros(len(states))
    values = continuation_values.copy()
    # (DC charge, local discharge, AC rate, export rate). Preserve production
    # modes; direct marketing permits disabling DC charge to make headroom.
    actions: list[TailAction] = [(1, 0, 0.0, 0.0), (1, 1, 0.0, 0.0)]
    actions += [(1, 0, rate, 0.0) for rate in charge_rates if rate > 0]
    if direct_marketing:
        actions += [(0, 0, 0.0, 0.0), (0, 1, 0.0, 0.0)]
        actions += [(0, 0, rate, 0.0) for rate in charge_rates if rate > 0]
        actions += [(dc, 1, 0.0, rate) for dc in (0, 1) for rate in export_rates if rate > 0]
    future_values: list[np.ndarray] = [np.empty(0)] * (len(arrays[0]) + 1)
    future_values[-1] = continuation_values.copy()
    for slot in reversed(range(len(arrays[0]))):
        price, load, pv, tariff = (array[slot] for array in arrays)
        best = np.full(len(states), -np.inf)
        best_operating = np.zeros(len(states))
        best_continuation = np.zeros(len(states))
        for action in actions:
            next_states = np.empty(len(states))
            rewards = np.empty(len(states))
            for i, energy in enumerate(states):
                result = _simulate_action(
                    bat=bat,
                    inv=inv,
                    energy_wh=energy,
                    action=action,
                    price=price,
                    load=load,
                    pv=pv,
                    tariff=tariff,
                    direct_marketing=direct_marketing,
                )
                rewards[i] = result["reward_euro"]
                next_states[i] = result["next_state_wh"]
            candidate_operating = rewards + np.interp(next_states, states, operating_values)
            candidate_continuation = np.interp(next_states, states, continuation_values)
            candidate = candidate_operating + candidate_continuation
            better = candidate > best
            best[better] = candidate[better]
            best_operating[better] = candidate_operating[better]
            best_continuation[better] = candidate_continuation[better]
        values = best
        operating_values = best_operating
        continuation_values = best_continuation
        future_values[slot] = best.copy()
    result = TailValueCurve(
        energy_wh=usable.tolist(),
        value_euro=values.tolist(),
        operating_value_euro=operating_values.tolist(),
        continuation_value_euro=continuation_values.tolist(),
        marginal_euro_per_kwh=(np.diff(values) / np.maximum(np.diff(usable), 1e-9) * 1000).tolist(),
        window_slots=len(arrays[0]),
    )
    result._trace_context = {
        "battery_parameters": battery.parameters,
        "inverter_parameters": inverter.parameters,
        "slot_duration_h": battery.slot_duration_h,
        "states": states,
        "future_values": future_values,
        "actions": actions,
        "prices": arrays[0],
        "load": arrays[1],
        "pv": arrays[2],
        "tariffs": arrays[3],
        "direct_marketing": direct_marketing,
    }
    return result
