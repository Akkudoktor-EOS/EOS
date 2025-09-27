from typing import Union

import requests
from loguru import logger
from monsterui.franken import (
    Card,
    Details,
    Div,
    DivLAligned,
    Grid,
    P,
    Summary,
    UkIcon,
)

from akkudoktoreos.config.config import SettingsEOS
from akkudoktoreos.core.emplan import (
    DDBCInstruction,
    EnergyManagementInstruction,
    EnergyManagementPlan,
    FRBCInstruction,
)
from akkudoktoreos.server.dash.components import Error
from akkudoktoreos.utils.datetimeutil import to_datetime


def InstructionCard(
    instruction: EnergyManagementInstruction,
    config: SettingsEOS,
) -> Card:
    """Creates a styled instruction card for displaying instruction details.

    This function generates a instruction card that is displayed in the UI with
    various sections such as instruction name, type, description, default value,
    current value, and error details. It supports both read-only and editable modes.

    Args:
        instruction (EnergyManagementInstruction): The instruction.

    Returns:
        Card: A styled Card component containing the instruction details.
    """
    if instruction.id is None:
        return Error("Instruction without id encountered. Can not handle")
    idx = instruction.id.find("@")
    resource_id = instruction.id[:idx] if idx != -1 else instruction.id
    execution_time = to_datetime(instruction.execution_time, as_string=True)
    description = instruction.type
    summary = None
    # Search an icon that fits to device_id
    if (
        config.devices
        and config.devices.batteries
        and any(
            battery_config.device_id == resource_id for battery_config in config.devices.batteries
        )
    ):
        # This is a battery
        if instruction.operation_mode_id in ("CHARGE",):
            icon = "battery-charging"
        else:
            icon = "battery"
    elif (
        config.devices
        and config.devices.electric_vehicles
        and any(
            electric_vehicle_config.device_id == resource_id
            for electric_vehicle_config in config.devices.electric_vehicles
        )
    ):
        # This is a car battery
        icon = "car"
    elif (
        config.devices
        and config.devices.home_appliances
        and any(
            home_appliance.device_id == resource_id
            for home_appliance in config.devices.home_appliances
        )
    ):
        # This is a home appliance
        icon = "washing-machine"
    else:
        icon = "play"
    if isinstance(instruction, (DDBCInstruction, FRBCInstruction)):
        summary = f"{instruction.operation_mode_id}"
        summary_detail = f"{instruction.operation_mode_factor}"
    return Card(
        Details(
            Summary(
                Grid(
                    Grid(
                        DivLAligned(
                            UkIcon(icon=icon),
                            P(execution_time),
                        ),
                        DivLAligned(
                            P(resource_id),
                        ),
                    ),
                    P(summary),
                    P(summary_detail),
                ),
                cls="list-none",
            ),
            Grid(
                P(description),
                P("TBD"),
            ),
        ),
        cls="w-full",
    )


def Plan(eos_host: str, eos_port: Union[str, int]) -> str:
    """Get current plan from server."""
    server = f"http://{eos_host}:{eos_port}"

    # Get current configuration from server
    try:
        result = requests.get(f"{server}/v1/config", timeout=10)
        result.raise_for_status()
    except requests.exceptions.HTTPError as err:
        detail = result.json()["detail"]
        return Error(f"Can not retrieve configuration from {server}: {err}, {detail}")
    config = SettingsEOS(**result.json())

    # Get the plan
    try:
        result = requests.get(f"{server}/v1/energy-management/plan", timeout=10)
        result.raise_for_status()
        plan_json = result.json()
    except requests.exceptions.HTTPError as e:
        detail = result.json()["detail"]
        warning_msg = f"Can not retrieve plan from {server}: {e}, {detail}"
        logger.warning(warning_msg)
        return Error(warning_msg)
    except Exception as e:
        warning_msg = f"Can not retrieve plan from {server}: {e}"
        logger.warning(warning_msg)
        return Error(warning_msg)

    plan = EnergyManagementPlan(**plan_json)

    rows = []
    for instruction in plan.instructions:
        rows.append(InstructionCard(instruction, config))
    return Div(*rows, cls="space-y-4")

    # return Div(f"Plan:\n{json.dumps(plan_json, indent=4)}")
