"""Repeated local clock times must dispatch by their real UTC instant."""

from akkudoktoreos.core.emplan import EnergyManagementPlan, FRBCInstruction
from akkudoktoreos.utils.datetimeutil import to_datetime


def test_repeated_hour_keeps_instruction_order_and_dispatch():
    first = to_datetime("2026-10-25T02:45:00+02:00").in_timezone("Europe/Berlin")
    second = to_datetime("2026-10-25T02:15:00+01:00").in_timezone("Europe/Berlin")
    plan = EnergyManagementPlan(id="fold", generated_at=first, instructions=[])
    later = FRBCInstruction(
        resource_id="battery1",
        actuator_id="battery1",
        execution_time=second,
        operation_mode_id="IDLE",
        operation_mode_factor=1.0,
    )
    earlier = FRBCInstruction(
        resource_id="battery1",
        actuator_id="battery1",
        execution_time=first,
        operation_mode_id="GRID_SUPPORT_IMPORT",
        operation_mode_factor=0.5,
    )
    plan.add_instruction(later)
    plan.add_instruction(earlier)
    assert [item.execution_time.timestamp() for item in plan.instructions] == [
        first.timestamp(),
        second.timestamp(),
    ]
    assert plan.valid_from is not None
    assert plan.valid_from.timestamp() == first.timestamp()
    assert plan.valid_until is None
    between = to_datetime("2026-10-25T02:00:00+01:00").in_timezone("Europe/Berlin")
    assert plan.get_active_instructions(between) == [earlier]
    assert plan.get_next_instruction(between) == later
    assert plan.get_active_instructions(second) == [later]
    assert plan.get_next_instruction(second) is None
