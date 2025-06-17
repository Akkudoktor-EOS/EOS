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

        # Check that valid_from matches the earliest execution_time
        assert plan.valid_from == fixed_now

        # instr2 has infinite duration so valid_until must be None
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

        active = plan.get_active_instructions(now=fixed_now)
        ids = {i.resource_id for i in active}
        assert ids == {"dev-1", "dev-3"}

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
                        power_envelope_elements = [
                            PEBCPowerEnvelopeElement(
                                duration = to_duration(10),
                                upper_limit = 1010.0,
                                lower_limit = 990.0,
                            ),
                        ],
                    ),
                ],
            ),
        ]

        for instr in instrs:
            plan.add_instruction(instr)

        assert len(plan.instructions) == len(instrs)
        # Check that get_instructions_for_device returns the right instructions
        assert any(
            instr for instr in plan.get_instructions_for_resource("actuatorA")
            if isinstance(instr, DDBCInstruction)
        )
