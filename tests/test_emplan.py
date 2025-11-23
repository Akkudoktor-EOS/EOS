from typing import Optional

import pytest

from akkudoktoreos.core.emplan import (
    BaseInstruction,
    CommodityQuantity,
    DDBCInstruction,
    EnergyManagementPlan,
    FRBCInstruction,
    OMBCInstruction,
    PEBCInstruction,
    PEBCPowerEnvelope,
    PEBCPowerEnvelopeElement,
    PPBCEndInterruptionInstruction,
    PPBCScheduleInstruction,
    PPBCStartInterruptionInstruction,
)
from akkudoktoreos.utils.datetimeutil import Duration, to_datetime, to_duration


@pytest.fixture
def fixed_now():
    return to_datetime("2025-06-01T12:00:00")


class TestEnergyManagementPlan:

    # ----------------------------------------------------------------------
    # Helpers (only used inside the class)
    # ----------------------------------------------------------------------
    def _make_instr(self, resource_id, execution_time, duration=None):
        if duration is None:
            instr = OMBCInstruction(
                id=resource_id,
                execution_time=execution_time,
                operation_mode_id="mode",
                operation_mode_factor=1.0,
            )
        else:
           instr = PEBCInstruction(
                id=resource_id,
                execution_time=execution_time,
                power_constraints_id="pc-123",
                power_envelopes=[
                    PEBCPowerEnvelope(
                        id="pebcpe@1234",
                        commodity_quantity=CommodityQuantity.ELECTRIC_POWER_L1,
                        power_envelope_elements=[
                            PEBCPowerEnvelopeElement(
                                duration=to_duration(duration),
                                upper_limit=1010.0,
                                lower_limit=990.0,
                            ),
                        ],
                    ),
                ],
            )

        return instr

    def _build_plan(self, instructions, now):
        plan = EnergyManagementPlan(
            id="plan-test",
            generated_at=now,
            instructions=instructions,
        )
        plan._update_time_range()
        return plan

    def test_add_instruction_and_time_range(self, fixed_now):
        plan = EnergyManagementPlan(
            id="plan-123",
            generated_at=fixed_now,
            instructions=[]
        )
        instr1 = OMBCInstruction(
            resource_id="dev-1",
            execution_time=fixed_now,
            operation_mode_id="mymode1",
            operation_mode_factor=1.0,
        )
        instr2 = OMBCInstruction(
            resource_id="dev-2",
            execution_time=fixed_now.add(minutes=5),
            operation_mode_id="mymode1",
            operation_mode_factor=1.0,
        )
        plan.add_instruction(instr1)
        plan.add_instruction(instr2)

        assert plan.valid_from == fixed_now
        assert plan.valid_until is None
        assert plan.instructions == [instr1, instr2]

    def test_clear(self, fixed_now):
        plan = EnergyManagementPlan(
            id="plan-123",
            generated_at=fixed_now,
            instructions=[
                OMBCInstruction(
                    resource_id="dev-1",
                    execution_time=fixed_now,
                    operation_mode_id="mymode1",
                    operation_mode_factor=1.0,
                )
            ],
        )
        plan.clear()
        assert plan.instructions == []
        assert plan.valid_until is None
        assert plan.valid_from is not None

    def test_get_next_instruction(self, fixed_now):
        instr1 = OMBCInstruction(
            resource_id="dev-1",
            execution_time=fixed_now.subtract(minutes=1),
            operation_mode_id="mymode1",
            operation_mode_factor=1.0,
        )
        instr2 = OMBCInstruction(
            resource_id="dev-2",
            execution_time=fixed_now.add(minutes=10),
            operation_mode_id="mymode1",
            operation_mode_factor=1.0,
        )
        instr3 = OMBCInstruction(
            resource_id="dev-3",
            execution_time=fixed_now.add(minutes=5),
            operation_mode_id="mymode1",
            operation_mode_factor=1.0,
        )
        plan = EnergyManagementPlan(
            id="plan-123",
            generated_at=fixed_now,
            instructions=[instr1, instr2, instr3],
        )
        plan._update_time_range()

        next_instr = plan.get_next_instruction(now=fixed_now)
        assert next_instr is not None
        assert next_instr.resource_id == "dev-3"

    def test_get_instructions_for_resource(self, fixed_now):
        instr1 = OMBCInstruction(
            resource_id="dev-1",
            execution_time=fixed_now,
            operation_mode_id="mymode1",
            operation_mode_factor=1.0,
        )
        instr2 = OMBCInstruction(
            resource_id="dev-2",
            execution_time=fixed_now,
            operation_mode_id="mymode1",
            operation_mode_factor=1.0,
        )
        plan = EnergyManagementPlan(
            id="plan-123",
            generated_at=fixed_now,
            instructions=[instr1, instr2],
        )
        dev1_instructions = plan.get_instructions_for_resource("dev-1")
        assert len(dev1_instructions) == 1
        assert dev1_instructions[0].resource_id == "dev-1"

    def test_add_various_instructions(self, fixed_now):
        plan = EnergyManagementPlan(
            id="plan-123",
            generated_at=fixed_now,
            instructions=[]
        )
        instrs = [
            DDBCInstruction(
                id="actuatorA@123",
                execution_time=fixed_now,
                actuator_id="actuatorA",
                operation_mode_id="mode123",
                operation_mode_factor=0.5,
            ),
            FRBCInstruction(
                id="actuatorB@456",
                execution_time=fixed_now.add(minutes=1),
                actuator_id="actuatorB",
                operation_mode_id="FRBC_Mode_1",
                operation_mode_factor=1.0,
            ),
            OMBCInstruction(
                id="controller@789",
                execution_time=fixed_now.add(minutes=2),
                operation_mode_id="OMBC_Mode_42",
                operation_mode_factor=0.8,
            ),
            PPBCEndInterruptionInstruction(
                id="end_int@001",
                execution_time=fixed_now.add(minutes=3),
                power_profile_id="profile-123",
                sequence_container_id="container-456",
                power_sequence_id="seq-789",
            ),
            PPBCStartInterruptionInstruction(
                id="start_int@002",
                execution_time=fixed_now.add(minutes=4),
                power_profile_id="profile-321",
                sequence_container_id="container-654",
                power_sequence_id="seq-987",
            ),
            PPBCScheduleInstruction(
                id="schedule@003",
                execution_time=fixed_now.add(minutes=5),
                power_profile_id="profile-999",
                sequence_container_id="container-888",
                power_sequence_id="seq-777",
            ),
            PEBCInstruction(
                id="pebc@004",
                execution_time=fixed_now.add(minutes=6),
                power_constraints_id="pc-123",
                power_envelopes=[
                    PEBCPowerEnvelope(
                        id="pebcpe@1234",
                        commodity_quantity=CommodityQuantity.ELECTRIC_POWER_L1,
                        power_envelope_elements=[
                            PEBCPowerEnvelopeElement(
                                duration=to_duration(10),
                                upper_limit=1010.0,
                                lower_limit=990.0,
                            ),
                        ],
                    ),
                ],
            ),
        ]

        for instr in instrs:
            plan.add_instruction(instr)

        assert len(plan.instructions) == len(instrs)
        assert any(
            instr for instr in plan.get_instructions_for_resource("actuatorA")
            if isinstance(instr, DDBCInstruction)
        )

    # -------------------------------------------
    # Special testing for get_active_instructions
    # -------------------------------------------

    def test_get_active_instructions(self, fixed_now):
        instr1 = OMBCInstruction(
            resource_id="dev-1",
            execution_time=fixed_now.subtract(minutes=1),
            operation_mode_id="mymode1",
            operation_mode_factor=1.0,
        )
        instr2 = OMBCInstruction(
            resource_id="dev-2",
            execution_time=fixed_now.add(minutes=1),
            operation_mode_id="mymode1",
            operation_mode_factor=1.0,
        )
        instr3 = OMBCInstruction(
            resource_id="dev-3",
            execution_time=fixed_now.subtract(minutes=10),
            operation_mode_id="mymode1",
            operation_mode_factor=1.0,
        )
        plan = EnergyManagementPlan(
            id="plan-123",
            generated_at=fixed_now,
            instructions=[instr1, instr2, instr3],
        )
        plan._update_time_range()

        resource_ids = plan.get_resources()
        assert resource_ids == ["dev-1", "dev-2", "dev-3"]

        active = plan.get_active_instructions(now=fixed_now)
        ids = {i.resource_id for i in active}
        assert ids == {"dev-1", "dev-3"}



    def test_get_active_instructions_with_duration(self, fixed_now):
        instr = self._make_instr(
            "dev-1",
            fixed_now.subtract(minutes=5),
            duration=Duration(minutes=10),
        )
        plan = self._build_plan([instr], fixed_now)
        active = plan.get_active_instructions(fixed_now)
        assert {i.resource_id for i in active} == {"dev-1"}

    def test_get_active_instructions_expired_duration(self, fixed_now):
        instr = self._make_instr(
            "dev-1",
            fixed_now.subtract(minutes=20),
            duration=Duration(minutes=10),
        )
        plan = self._build_plan([instr], fixed_now)
        assert plan.get_active_instructions(fixed_now) == []

    def test_get_active_instructions_end_exactly_now_not_active(self, fixed_now):
        instr = self._make_instr(
            "dev-1",
            fixed_now.subtract(minutes=10),
            duration=Duration(minutes=10),
        )
        plan = self._build_plan([instr], fixed_now)
        assert plan.get_active_instructions(fixed_now) == []

    def test_get_active_instructions_latest_supersedes(self, fixed_now):
        instr1 = self._make_instr(
            "dev-1",
            fixed_now.subtract(minutes=10),
            duration=Duration(minutes=30),
        )
        instr2 = self._make_instr("dev-1", fixed_now.subtract(minutes=1))
        plan = self._build_plan([instr1, instr2], fixed_now)

        active = plan.get_active_instructions(fixed_now)
        assert len(active) == 1
        assert active[0] is instr2

    def test_get_active_instructions_mixed_resources(self, fixed_now):
        instr1 = self._make_instr(
            "r1",
            fixed_now.subtract(minutes=5),
            duration=Duration(minutes=10),
        )
        instr2 = self._make_instr("r2", fixed_now.subtract(minutes=1))
        instr3 = self._make_instr("r3", fixed_now.add(minutes=10))
        plan = self._build_plan([instr1, instr2, instr3], fixed_now)

        ids = {i.resource_id for i in plan.get_active_instructions(fixed_now)}
        assert ids == {"r1", "r2"}

    def test_get_active_instructions_start_exactly_now(self, fixed_now):
        instr = self._make_instr("dev-1", fixed_now)
        plan = self._build_plan([instr], fixed_now)
        assert {i.resource_id for i in plan.get_active_instructions(fixed_now)} == {"dev-1"}

    def test_get_active_instructions_no_active(self, fixed_now):
        instr = self._make_instr("dev-1", fixed_now.add(minutes=1))
        plan = self._build_plan([instr], fixed_now)
        assert plan.get_active_instructions(fixed_now) == []

    def test_get_active_instructions_future_does_not_override_until_reached(self, fixed_now):
        instr1 = self._make_instr("dev-1", fixed_now.subtract(minutes=5))
        instr2 = self._make_instr("dev-1", fixed_now.add(minutes=5))
        plan = self._build_plan([instr1, instr2], fixed_now)

        active_before = plan.get_active_instructions(fixed_now)
        assert {i.resource_id for i in active_before} == {"dev-1"}

    def test_get_active_instructions_future_overrides_once_time_reached(self, fixed_now):
        exec_future = fixed_now.add(minutes=5)
        instr1 = self._make_instr("dev-1", fixed_now.subtract(minutes=5))
        instr2 = self._make_instr("dev-1", exec_future)

        plan = self._build_plan([instr1, instr2], fixed_now)

        active_before = plan.get_active_instructions(fixed_now)
        assert {i.resource_id for i in active_before} == {"dev-1"}

        active_after = plan.get_active_instructions(exec_future)
        assert {i.resource_id for i in active_after} == {"dev-1"}
