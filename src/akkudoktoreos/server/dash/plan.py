from typing import Optional, Union

import requests
from bokeh.models import ColumnDataSource, LinearAxis, Range1d
from bokeh.plotting import figure
from loguru import logger
from monsterui.franken import (
    Card,
    Details,
    Div,
    DivLAligned,
    Grid,
    LabelCheckboxX,
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
from akkudoktoreos.optimization.optimization import OptimizationSolution
from akkudoktoreos.server.dash.bokeh import Bokeh
from akkudoktoreos.server.dash.components import Error
from akkudoktoreos.utils.datetimeutil import to_datetime

# bar width for 1 hour bars (time given in millseconds)
BAR_WIDTH_1HOUR = 1000 * 60 * 60

# Current state of solution displayed
solution_visible: dict[str, bool] = {}
solution_color: dict[str, str] = {}


def validate_source(source: ColumnDataSource, x_col: str = "date_time") -> None:
    data = source.data

    # 1. Source has data at all
    if not data:
        raise ValueError("ColumnDataSource has no data.")

    # 2. x_col must be present
    if x_col not in data:
        raise ValueError(f"Missing expected x-axis column '{x_col}' in source.")

    # 3. All columns must have equal length
    lengths = {len(v) for v in data.values()}
    if len(lengths) != 1:
        raise ValueError(f"ColumnDataSource columns have mismatched lengths: {lengths}")

    # 4. Must have at least one non-x column
    y_columns = [c for c in data.keys() if c != x_col]
    if not y_columns:
        raise ValueError("No y-value columns found for plotting (only x-axis present).")

    # 5. Each y-column must have at least one valid value
    for col in y_columns:
        values = [v for v in data[col] if v is not None]
        if not values:
            raise ValueError(f"Column '{col}' contains only None/NaN or is empty.")


def SolutionCard(solution: OptimizationSolution, config: SettingsEOS, data: Optional[dict]) -> Grid:
    """Creates a optimization solution card.

    Args:
        data (Optional[dict]): Incoming data containing action and category for processing.
    """
    category = "solution"
    if data and data.get("category", None) == category:
        # This data is for us
        if data.get("action", None) == "visible":
            renderer = data.get("renderer", None)
            if renderer:
                solution_visible[renderer] = bool(data.get(f"{renderer}-visible", False))

    df = solution.data.to_dataframe()
    if df.empty or len(df.columns) <= 1:
        raise ValueError(f"DataFrame is empty or missing plottable columns: {list(df.columns)}")
    if "date_time" not in df.columns:
        raise ValueError(f"DataFrame is missing column 'date_time': {list(df.columns)}")

    source = ColumnDataSource(df)
    validate_source(source)

    # Calculate minimum and maximum Range
    energy_wh_min = 0.0
    energy_wh_max = 0.0
    amt_kwh_min = 0.0
    amt_kwh_max = 0.0
    amt_min = 0.0
    amt_max = 0.0
    soc_factor_min = 0.0
    soc_factor_max = 1.0
    for col in df.columns:
        if col.endswith("energy_wh"):
            energy_wh_min = min(energy_wh_min, float(df[col].min()))
            energy_wh_max = max(energy_wh_max, float(df[col].max()))
        elif col.endswith("amt_kwh"):
            amt_kwh_min = min(amt_kwh_min, float(df[col].min()))
            amt_kwh_max = max(amt_kwh_max, float(df[col].max()))
        elif col.endswith("amt"):
            amt_min = min(amt_min, float(df[col].min()))
            amt_max = max(amt_max, float(df[col].max()))
        else:
            continue
    # Adjust to similar y-axis 0-point
    # First get the maximum factor for the min value related the maximum value
    min_max_factor = max(
        (energy_wh_min * -1.0) / energy_wh_max,
        (amt_kwh_min * -1.0) / amt_kwh_max,
        (amt_min * -1.0) / amt_max,
        (soc_factor_min * -1.0) / soc_factor_max,
    )
    # Adapt the min values to have the same relative min/max factor on all y-axis
    energy_wh_min = min_max_factor * energy_wh_max * -1.0
    amt_kwh_min = min_max_factor * amt_kwh_max * -1.0
    amt_min = min_max_factor * amt_max * -1.0
    soc_factor_min = min_max_factor * soc_factor_max * -1.0

    plot = figure(
        title="Optimization Solution",
        x_axis_type="datetime",
        x_axis_label="Datetime",
        y_axis_label="Amount [currency]",
        sizing_mode="stretch_width",
        y_range=Range1d(amt_min, amt_max),
        height=400,
    )
    plot.extra_y_ranges = {
        "energy_wh": Range1d(energy_wh_min, energy_wh_max),
        "soc_factor": Range1d(soc_factor_min, soc_factor_max),
        "amt_kwh": Range1d(amt_kwh_min, amt_kwh_max),
    }
    # y2 axis
    y2_axis = LinearAxis(y_range_name="energy_wh", axis_label="Energy [Wh]")
    # y2_axis.axis_label_text_color = "black"
    plot.add_layout(y2_axis, "left")
    # y3 axis
    y3_axis = LinearAxis(y_range_name="soc_factor", axis_label="SoC [0.0..1.0]")
    # y3_axis.axis_label_text_color = "black"
    plot.add_layout(y3_axis, "left")
    # y4 axis
    y4_axis = LinearAxis(y_range_name="amt_kwh", axis_label="Electricty Price [Money/kWh]")
    y4_axis.axis_label_text_color = "red"
    plot.add_layout(y4_axis, "right")
    plot.toolbar.autohide = True

    # Create line renderers for each column
    renderers = {}
    colors = ["black", "blue", "cyan", "green", "orange", "pink", "purple"]

    for i, col in enumerate(df.columns):
        if col == "date_time":
            continue
        if col in solution_visible:
            visible = solution_visible[col]
        else:
            visible = True
            solution_visible[col] = visible
        if col in solution_color:
            color = solution_color[col]
        elif col == "pv_prediction_energy_wh":
            color = "yellow"
            solution_color[col] = color
        elif col == "elec_price_prediction_amt_kwh":
            color = "red"
            solution_color[col] = color
        else:
            color = colors[i % len(colors)]
            solution_color[col] = color
        if visible:
            if col == "pv_prediction_energy_wh":
                r = plot.vbar(
                    x="date_time",
                    top=col,
                    source=source,
                    width=BAR_WIDTH_1HOUR * 0.8,
                    legend_label=col,
                    color=color,
                    y_range_name="energy_wh",
                    level="underlay",
                )
            elif col.endswith("energy_wh"):
                r = plot.line(
                    x="date_time",
                    y=col,
                    source=source,
                    legend_label=col,
                    color=color,
                    y_range_name="energy_wh",
                )
            elif col.endswith("soc_factor"):
                r = plot.line(
                    x="date_time",
                    y=col,
                    source=source,
                    legend_label=col,
                    color=color,
                    y_range_name="soc_factor",
                )
            elif col.endswith("amt_kwh"):
                r = plot.line(
                    x="date_time",
                    y=col,
                    source=source,
                    legend_label=col,
                    color=color,
                    y_range_name="amt_kwh",
                )
            elif col.endswith("amt"):
                r = plot.line(
                    x="date_time",
                    y=col,
                    source=source,
                    legend_label=col,
                    color=color,
                )
            else:
                raise ValueError(f"Unexpected column name: {col}")

        else:
            r = None
        renderers[col] = r

    # --- CheckboxGroup to toggle datasets ---
    plot.legend.visible = False  # no lend at plot
    Checkbox = Grid(
        *[
            LabelCheckboxX(
                label=renderer,
                id=f"{renderer}-visible",
                name=f"{renderer}-visible",
                value="true",
                checked=solution_visible[renderer],
                hx_post="/eosdash/plan",
                hx_target="#page-content",
                hx_swap="innerHTML",
                hx_vals='js:{ "category": "solution", "action": "visible", "renderer": "'
                + f"{renderer}"
                + '"}',
                lbl_cls=f"text-{solution_color[renderer]}-500",
            )
            for renderer in list(renderers.keys())
        ],
        cols=1,
    )

    return Grid(
        Bokeh(plot),
        Card(
            Checkbox,
        ),
        cls="w-full space-y-3 space-x-3",
    )


def InstructionCard(
    instruction: EnergyManagementInstruction, config: SettingsEOS, data: Optional[dict]
) -> Card:
    """Creates a styled instruction card for displaying instruction details.

    This function generates a instruction card that is displayed in the UI with
    various sections such as instruction name, type, description, default value,
    current value, and error details. It supports both read-only and editable modes.

    Args:
        instruction (EnergyManagementInstruction): The instruction.
        data (Optional[dict]): Incoming data containing action and category for processing.

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


def Plan(eos_host: str, eos_port: Union[str, int], data: Optional[dict] = None) -> Div:
    """Generates the plan dashboard layout.

    Args:
        eos_host (str): The hostname of the EOS server.
        eos_port (Union[str, int]): The port of the EOS server.
        data (Optional[dict], optional): Incoming data to trigger plan actions. Defaults to None.

    Returns:
        Div: A `Div` component containing the assembled admin interface.
    """
    server = f"http://{eos_host}:{eos_port}"

    # Get current configuration from server
    try:
        result = requests.get(f"{server}/v1/config", timeout=10)
        result.raise_for_status()
    except requests.exceptions.HTTPError as err:
        detail = result.json()["detail"]
        return Error(f"Can not retrieve configuration from {server}: {err}, {detail}")
    config = SettingsEOS(**result.json())

    # Get the optimization solution
    try:
        result = requests.get(f"{server}/v1/energy-management/optimization/solution", timeout=10)
        result.raise_for_status()
        solution_json = result.json()
    except requests.exceptions.HTTPError as e:
        detail = result.json()["detail"]
        warning_msg = f"Can not retrieve optimization solution from {server}: {e}, {detail}"
        logger.warning(warning_msg)
        return Error(warning_msg)
    except Exception as e:
        warning_msg = f"Can not retrieve optimization solution from {server}: {e}"
        logger.warning(warning_msg)
        return Error(warning_msg)

    solution = OptimizationSolution(**solution_json)

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

    plan = EnergyManagementPlan(**plan_json, data=data)

    rows = [
        SolutionCard(solution, config, data=data),
    ]
    for instruction in plan.instructions:
        rows.append(InstructionCard(instruction, config, data=data))
    return Div(*rows, cls="space-y-4")

    # return Div(f"Plan:\n{json.dumps(plan_json, indent=4)}")
