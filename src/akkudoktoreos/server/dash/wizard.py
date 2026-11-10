"""Setup wizard for EOSdash.

The wizard walks a new user through the configuration EOS needs before it can predict and
optimize. Every step explains a small group of settings, gives an example, reports what is
still missing and shows the configuration cards of exactly these settings - the same cards as
on the Config page.

The steps are data: add a ``WizardStep`` to ``WIZARD_STEPS`` to add a step.

The wizard keeps no state of its own. The current step travels with the requests
(``wizard_step``) and whether a step is done is derived from the EOS configuration. The
configuration is read and written through the EOS server only.

The configuration cards send their updates to ``/eosdash/configuration``. The wizard content
is wrapped in an element that adds ``wizard_step`` to all requests from within (``hx-vals`` is
inherited), which makes that route answer with the wizard instead of the Config page.
"""

import json
from dataclasses import dataclass
from typing import Any, Callable, Optional, Union

import requests
from fasthtml.common import Div
from monsterui.franken import (
    H3,
    Card,
    DivHStacked,
    DivLAligned,
    P,
    UkIcon,
)

from akkudoktoreos.config.config import ConfigEOS
from akkudoktoreos.server.dash.components import (
    Badge,
    ConfigButton,
    Error,
    Success,
)
from akkudoktoreos.server.dash.configuration import (
    config_card,
    config_update,
    create_config_details,
    get_config,
)
from akkudoktoreos.server.dash.context import request_url_for
from akkudoktoreos.server.dash.markdown import Markdown

WIZARD_PATH = "/eosdash/wizard"


# ---------------------------------------------------------------------------
# Step definition
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class WizardStep:
    """A step of the setup wizard.

    Attributes:
        id: Identifier of the step, used in requests.
        title: Title of the step.
        intro: Explanation of the step (Markdown).
        example: Example for the settings of the step (Markdown).
        config_names: Dotted names of the configuration values to show. ``{algorithm}`` is
            replaced by the lower case name of the configured optimization algorithm.
        check: Function that takes the configuration details and returns what is still to do
            in this step, one sentence each. An empty list means the step is done.
    """

    id: str
    title: str
    intro: str
    example: str = ""
    config_names: tuple[str, ...] = ()
    check: Optional[Callable[[dict[str, dict]], list[str]]] = None


def config_value(config_details: dict[str, dict], name: str, default: Any = None) -> Any:
    """Return the current value of a configuration value.

    Args:
        config_details: Configuration details, as created by ``create_config_details``.
        name: Dotted name of the configuration value.
        default: Value to return if the configuration value is unknown.
    """
    try:
        return json.loads(config_details[name]["value"])
    except (KeyError, TypeError, ValueError):
        return default


def step_config_names(step: WizardStep, config_details: dict[str, dict]) -> list[str]:
    """Return the names of the configuration values a step shows for this configuration."""
    algorithm = str(config_value(config_details, "optimization.algorithm", "")).lower()
    names = [name.format(algorithm=algorithm) for name in step.config_names]
    return [name for name in names if name in config_details]


# ---------------------------------------------------------------------------
# Checks
# ---------------------------------------------------------------------------


def check_location(config_details: dict[str, dict]) -> list[str]:
    """Report what is still to do for the location."""
    latitude = config_value(config_details, "general.latitude")
    longitude = config_value(config_details, "general.longitude")
    if latitude is None or longitude is None:
        return ["Latitude and longitude are not set."]

    todo = []
    try:
        defaults = (
            json.loads(config_details["general.latitude"]["default"]),
            json.loads(config_details["general.longitude"]["default"]),
        )
    except (KeyError, TypeError, ValueError):
        defaults = None
    if defaults == (latitude, longitude):
        todo.append(
            f"Latitude and longitude are still the default values ({latitude}, {longitude}). "
            "Set the location of your home."
        )
    if not config_value(config_details, "general.timezone"):
        todo.append(
            "EOS has no time zone for this location. Check latitude and longitude or set "
            "general.timezone_override."
        )
    return todo


def check_horizon(config_details: dict[str, dict]) -> list[str]:
    """Report what is still to do for the horizons and the energy management."""
    todo = []
    algorithm = str(config_value(config_details, "optimization.algorithm", "")).lower()
    horizon_name = f"optimization.{algorithm}.horizon_hours"
    horizon = config_value(config_details, horizon_name)
    prediction = config_value(config_details, "prediction.hours")
    if isinstance(horizon, (int, float)) and (prediction is None or prediction < horizon):
        todo.append(
            f"prediction.hours ({prediction}) is shorter than {horizon_name} ({horizon}). "
            "Optimization runs fail until the predictions cover the optimization horizon."
        )
    if config_value(config_details, "ems.mode") == "DISABLED":
        todo.append(
            "ems.mode is DISABLED: EOS does not run the energy management. Set it to "
            "OPTIMIZATION to get a plan, or to PREDICTION to only update the predictions."
        )
    return todo


# ---------------------------------------------------------------------------
# Steps
# ---------------------------------------------------------------------------

WIZARD_STEPS: tuple[WizardStep, ...] = (
    WizardStep(
        id="start",
        title="Start",
        intro="""
This assistant takes you through the settings EOS needs before it can predict and optimize.

Each step explains a few settings, gives an example and shows the configuration cards of
these settings. The cards are the same as on the **Config** page:

- Click a card to open it. It shows the description, the type and the default of the setting.
- Type the new value into the **update** field and press Enter, or choose a value and press
  **Set**. The value is checked by EOS; if EOS rejects it, the card shows why.
- A new value is used by EOS right away. By default EOS also saves it to the configuration
  file within a minute, so it survives a restart of EOS. If you switched the automatic saving
  off, save the configuration in the last step.

Above the explanation you see all steps. A check mark means EOS has what it needs from that
step, a warning sign means something is left to do. You can leave the assistant at any time
and come back later: it reads the state from the EOS configuration.

If EOS is already configured, save a copy of the configuration on the **Admin** page before
you change it.
""",
    ),
    WizardStep(
        id="location",
        title="Location",
        intro="""
EOS needs to know where your home is. The weather forecast and the PV forecast are made for
this location, and EOS looks up your **time zone** from it. All times in the predictions and
in the plan are local times of this time zone.

- `general.latitude` - decimal degrees, north is positive.
- `general.longitude` - decimal degrees, east is positive.
- `general.timezone_override` - only needed if the time zone EOS looks up is not the one you
  want. Leave it at `null` otherwise.
""",
        example="""
A house in Munich: latitude `48.137`, longitude `11.575`. EOS looks up the time zone
`Europe/Berlin`. Three decimal places are precise enough (about 100 m).

To set the time zone yourself use an IANA name such as `Europe/Vienna`.
""",
        config_names=("general.latitude", "general.longitude", "general.timezone_override"),
        check=check_location,
    ),
    WizardStep(
        id="horizon",
        title="Horizons and energy management",
        intro="""
Two horizons decide how far EOS looks ahead, and the energy management decides whether and
how often EOS works on its own.

- `prediction.hours` - how many hours into the future EOS predicts PV yield, load and prices.
- `optimization.<algorithm>.horizon_hours` - how many hours the plan covers. The predictions
  must cover at least this horizon, otherwise the optimization fails.
- `ems.mode` - what the energy management does in each run: `DISABLED` nothing,
  `PREDICTION` updates the predictions, `OPTIMIZATION` updates the predictions and
  calculates a new plan. Outside of Home Assistant the default is `DISABLED`, so EOS stays
  idle until you change it.
- `ems.interval` - seconds between two energy management runs.
""",
        example="""
A common setup: `prediction.hours` = `48`, `horizon_hours` = `24`, `ems.mode` =
`OPTIMIZATION` and `ems.interval` = `300`. EOS then predicts two days ahead and calculates
a plan for the next 24 hours every five minutes.

Use `PREDICTION` while you are still setting up the forecasts and want to look at them on the
**Prediction** page before EOS optimizes.
""",
        config_names=(
            "prediction.hours",
            "optimization.algorithm",
            "optimization.{algorithm}.horizon_hours",
            "ems.mode",
            "ems.interval",
        ),
        check=check_horizon,
    ),
    WizardStep(
        id="finish",
        title="Finish",
        intro="""
Below is what the steps of this assistant found. Go back to a step to complete it.

EOS saves changes of the configuration to the configuration file on its own if
`general.config_save_mode` is `AUTOMATIC`, which is the default. With `MANUAL` your settings
are lost when EOS restarts unless you save them. **Save configuration** writes the
configuration file right now in both modes.

This assistant does not cover everything yet. Set up the following on the **Config** page:

- the PV system (`pvforecast`),
- electricity prices, grid fees and the feed-in tariff (`elecprice`, `elecfee`,
  `feedintariff`),
- the load forecast (`load`),
- battery, inverter, electric vehicle and home appliances (`devices`),
- measurements and the connection to your home automation (`measurement`, `adapter`).

Then look at the **Prediction** page to check the forecasts and at the **Plan** page for the
result of the optimization.
""",
        config_names=("general.config_save_mode",),
    ),
)


def wizard_step(step_id: Optional[str]) -> WizardStep:
    """Return the step with the given id, or the first step if there is no such step."""
    for step in WIZARD_STEPS:
        if step.id == step_id:
            return step
    return WIZARD_STEPS[0]


def step_todo(step: WizardStep, config_details: dict[str, dict]) -> list[str]:
    """Return what is still to do in a step. An empty list means the step is done."""
    return step.check(config_details) if step.check else []


# ---------------------------------------------------------------------------
# Actions
# ---------------------------------------------------------------------------


def save_config(eos_host: str, eos_port: Union[str, int]) -> Any:
    """Let the EOS server save the configuration to its configuration file.

    Returns:
        A success or an error message.
    """
    server = f"http://{eos_host}:{eos_port}"
    try:
        result = requests.put(f"{server}/v1/config/file", timeout=10)
        result.raise_for_status()
        config_file_path = result.json()["general"]["config_file_path"]
    except requests.exceptions.HTTPError as e:
        try:
            detail = result.json().get("detail", result.text)
        except ValueError:
            detail = result.text
        return Error(f"Can not save the configuration on '{eos_host}': {e}, {detail}")
    except Exception as e:
        return Error(f"Can not save the configuration on '{eos_host}': {e}")
    return Success(f"Saved the configuration to '{config_file_path}' on '{eos_host}'.")


# ---------------------------------------------------------------------------
# Visual representation
# ---------------------------------------------------------------------------


def _step_button(label: Any, step: WizardStep, **kwargs: Any) -> Any:
    """Button that opens a step."""
    return ConfigButton(
        label,
        hx_get=request_url_for(WIZARD_PATH),
        hx_target="#page-content",
        hx_swap="innerHTML",
        hx_vals=json.dumps({"wizard_step": step.id}),
        **kwargs,
    )


def _todo_list(todo: list[str]) -> Any:
    """What is still to do in a step, or that nothing is left to do."""
    if not todo:
        return DivLAligned(UkIcon("circle-check"), P("Nothing left to do in this step."))
    return Div(
        *[DivLAligned(UkIcon("triangle-alert", cls="shrink-0"), P(item)) for item in todo],
        cls="space-y-2",
    )


def WizardSteps(current: WizardStep, config_details: dict[str, dict]) -> Card:
    """Overview of all steps with their state and the navigation between the steps."""
    index = WIZARD_STEPS.index(current)
    badges = []
    for number, step in enumerate(WIZARD_STEPS):
        if step.check is None:
            icon = "circle"
        elif step_todo(step, config_details):
            icon = "triangle-alert"
        else:
            icon = "circle-check"
        badges.append(
            Div(
                Badge(
                    icon, f"{number + 1}. {step.title}", "accent" if step is current else "muted"
                ),
                hx_get=request_url_for(WIZARD_PATH),
                hx_target="#page-content",
                hx_swap="innerHTML",
                hx_vals=json.dumps({"wizard_step": step.id}),
                id=f"wizard-step-{step.id}",
                cls="cursor-pointer",
            )
        )
    return Card(
        Div(*badges, cls="flex flex-wrap gap-2"),
        DivHStacked(
            _step_button("Back", WIZARD_STEPS[index - 1], id="wizard-back") if index > 0 else None,
            _step_button("Next", WIZARD_STEPS[index + 1], id="wizard-next")
            if index + 1 < len(WIZARD_STEPS)
            else None,
            cls="gap-2 mt-4",
        ),
        header=H3(f"Step {index + 1} of {len(WIZARD_STEPS)}: {current.title}"),
    )


def Wizard(
    eos_host: str,
    eos_port: Union[str, int],
    data: Optional[dict] = None,
) -> Div:
    """Create the setup wizard page.

    Args:
        eos_host (str): The hostname of the EOS server.
        eos_port (Union[str, int]): The port of the EOS server.
        data (Optional[dict], optional): Request data: the step to show in ``wizard_step`` and
            optionally an ``action`` (``update`` a configuration value, ``save_to_file``).

    Returns:
        Div: The wizard page showing one step.
    """
    data = data or {}
    step = wizard_step(data.get("wizard_step"))

    save_status = None
    action = data.get("action")
    if action == "update":
        config_update(eos_host, eos_port, data)
    elif action == "save_to_file":
        save_status = save_config(eos_host, eos_port)

    # (Re-)read the configuration to show the actual state
    config_details = create_config_details(ConfigEOS, get_config(eos_host, eos_port))

    content: list[Any] = [
        WizardSteps(step, config_details),
        Card(Markdown(step.intro)),
    ]
    if step.example:
        content.append(Card(Markdown(step.example), header=H3("Example")))

    if step.check:
        content.append(Card(_todo_list(step_todo(step, config_details)), header=H3("Status")))

    for name in step_config_names(step, config_details):
        card = config_card(config_details[name], config_details)
        if card is not None:
            content.append(card)

    if step is WIZARD_STEPS[-1]:
        summary = [
            Div(
                P(f"{WIZARD_STEPS.index(other) + 1}. {other.title}", cls="font-bold"),
                _todo_list(step_todo(other, config_details)),
                cls="space-y-2",
            )
            for other in WIZARD_STEPS
            if other.check
        ]
        content.append(Card(*summary, header=H3("Status"), cls="space-y-4"))
        content.append(
            Card(
                DivHStacked(
                    Div(
                        ConfigButton(
                            "Save configuration",
                            hx_post=request_url_for(WIZARD_PATH),
                            hx_target="#page-content",
                            hx_swap="innerHTML",
                            hx_vals=json.dumps({"action": "save_to_file"}),
                            id="wizard-save",
                        ),
                        cls="shrink-0",
                    ),
                    save_status,
                    cls="gap-4",
                ),
            )
        )

    # hx-vals is inherited: all requests from within the wizard carry the current step
    return Div(
        *content,
        hx_vals=json.dumps({"wizard_step": step.id}),
        id="wizard",
        cls="space-y-4",
    )
