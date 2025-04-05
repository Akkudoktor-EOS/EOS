"""Admin UI components for EOS Dashboard.

This module provides functions to generate administrative UI components
for the EOS dashboard.
"""

import json
from pathlib import Path
from typing import Any, Optional, Union

import requests
from fasthtml.common import Select
from monsterui.foundations import stringify
from monsterui.franken import (  # Select, TODO: Select from FrankenUI does not work - using Select from FastHTML instead
    H3,
    Button,
    ButtonT,
    Card,
    Details,
    Div,
    DivHStacked,
    DividerLine,
    Grid,
    Input,
    Options,
    P,
    Summary,
    UkIcon,
)
from platformdirs import user_config_dir

from akkudoktoreos.core.logging import get_logger
from akkudoktoreos.server.dash.components import Error, Success
from akkudoktoreos.server.dash.configuration import get_nested_value
from akkudoktoreos.utils.datetimeutil import to_datetime

logger = get_logger(__name__)

# Directory to export files to, or to import files from
export_import_directory = Path(user_config_dir("net.akkudoktor.eosdash", "akkudoktor"))


def AdminButton(*c: Any, cls: Optional[Union[str, tuple]] = None, **kwargs: Any) -> Button:
    """Creates a styled button for administrative actions.

    Args:
        *c (Any): Positional arguments representing the button's content.
        cls (Optional[Union[str, tuple]]): Additional CSS classes for styling. Defaults to None.
        **kwargs (Any): Additional keyword arguments passed to the `Button`.

    Returns:
        Button: A styled `Button` component for admin actions.
    """
    new_cls = f"{ButtonT.primary}"
    if cls:
        new_cls += f" {stringify(cls)}"
    kwargs["cls"] = new_cls
    return Button(*c, submit=False, **kwargs)


def AdminConfig(
    eos_host: str, eos_port: Union[str, int], data: Optional[dict], config: Optional[dict[str, Any]]
) -> tuple[str, Union[Card, list[Card]]]:
    """Creates a configuration management card with save-to-file functionality.

    Args:
        eos_host (str): The hostname of the EOS server.
        eos_port (Union[str, int]): The port of the EOS server.
        data (Optional[dict]): Incoming data containing action and category for processing.

    Returns:
        tuple[str, Union[Card, list[Card]]]: A tuple containing the configuration category label and the `Card` UI component.
    """
    server = f"http://{eos_host}:{eos_port}"
    eos_hostname = "EOS server"
    eosdash_hostname = "EOSdash server"

    category = "configuration"
    # save config file
    status = (None,)
    config_file_path = "<unknown>"
    try:
        if config:
            config_file_path = get_nested_value(config, ["general", "config_file_path"])
    except:
        pass
    # export config file
    export_to_file_next_tag = to_datetime(as_string="YYYYMMDDHHmmss")
    export_to_file_status = (None,)
    # import config file
    import_from_file_status = (None,)

    if data and data.get("category", None) == category:
        # This data is for us
        if data["action"] == "save_to_file":
            # Safe current configuration to file
            try:
                result = requests.put(f"{server}/v1/config/file")
                result.raise_for_status()
                config_file_path = result.json()["general"]["config_file_path"]
                status = Success(f"Saved to '{config_file_path}' on '{eos_hostname}'")
            except requests.exceptions.HTTPError as e:
                detail = result.json()["detail"]
                status = Error(
                    f"Can not save actual config to file on '{eos_hostname}': {e}, {detail}"
                )
            except Exception as e:
                status = Error(f"Can not save actual config to file on '{eos_hostname}': {e}")
        elif data["action"] == "export_to_file":
            # Export current configuration to file
            export_to_file_tag = data.get("export_to_file_tag", export_to_file_next_tag)
            export_to_file_path = export_import_directory.joinpath(
                f"eos_config_{export_to_file_tag}.json"
            )
            try:
                if not config:
                    raise ValueError(f"No config from '{eos_hostname}'")
                export_to_file_path.parent.mkdir(parents=True, exist_ok=True)
                with export_to_file_path.open("w", encoding="utf-8", newline="\n") as fd:
                    json.dump(config, fd, indent=4, sort_keys=True)
                export_to_file_status = Success(
                    f"Exported to '{export_to_file_path}' on '{eosdash_hostname}'"
                )
            except requests.exceptions.HTTPError as e:
                detail = result.json()["detail"]
                export_to_file_status = Error(
                    f"Can not export actual config to '{export_to_file_path}' on '{eosdash_hostname}': {e}, {detail}"
                )
            except Exception as e:
                export_to_file_status = Error(
                    f"Can not export actual config to '{export_to_file_path}' on '{eosdash_hostname}': {e}"
                )
        elif data["action"] == "import_from_file":
            import_file_name = data.get("import_file_name", None)
            import_from_file_pathes = list(
                export_import_directory.glob("*.json")
            )  # expand generator object
            import_file_path = None
            for f in import_from_file_pathes:
                if f.name == import_file_name:
                    import_file_path = f
            if import_file_path:
                try:
                    with import_file_path.open("r", encoding="utf-8", newline=None) as fd:
                        import_config = json.load(fd)
                    result = requests.put(f"{server}/v1/config", json=import_config)
                    result.raise_for_status()
                    import_from_file_status = Success(
                        f"Config imported from '{import_file_path}' on '{eosdash_hostname}'"
                    )
                except requests.exceptions.HTTPError as e:
                    detail = result.json()["detail"]
                    import_from_file_status = Error(
                        f"Can not import config from '{import_file_name}' on '{eosdash_hostname}' {e}, {detail}"
                    )
                except Exception as e:
                    import_from_file_status = Error(
                        f"Can not import config from '{import_file_name}' on '{eosdash_hostname}' {e}"
                    )
            else:
                import_from_file_status = Error(
                    f"Can not import config from '{import_file_name}', not found in '{export_import_directory}' on '{eosdash_hostname}'"
                )

    # Update for display, in case we added a new file before
    import_from_file_names = [f.name for f in list(export_import_directory.glob("*.json"))]

    return (
        category,
        [
            Card(
                Details(
                    Summary(
                        Grid(
                            DivHStacked(
                                UkIcon(icon="play"),
                                AdminButton(
                                    "Save to file",
                                    hx_post="/eosdash/admin",
                                    hx_target="#page-content",
                                    hx_swap="innerHTML",
                                    hx_vals='{"category": "configuration", "action": "save_to_file"}',
                                ),
                                P(f"'{config_file_path}' on '{eos_hostname}'"),
                            ),
                            status,
                        ),
                        cls="list-none",
                    ),
                    P(f"Safe actual configuration to '{config_file_path}' on '{eos_hostname}'."),
                ),
            ),
            Card(
                Details(
                    Summary(
                        Grid(
                            DivHStacked(
                                UkIcon(icon="play"),
                                AdminButton(
                                    "Export to file",
                                    hx_post="/eosdash/admin",
                                    hx_target="#page-content",
                                    hx_swap="innerHTML",
                                    hx_vals='js:{"category": "configuration", "action": "export_to_file", "export_to_file_tag": document.querySelector("[name=\'chosen_export_file_tag\']").value }',
                                ),
                                P("'eos_config_"),
                                Input(
                                    id="export_file_tag",
                                    name="chosen_export_file_tag",
                                    value=export_to_file_next_tag,
                                ),
                                P(".json'"),
                            ),
                            export_to_file_status,
                        ),
                        cls="list-none",
                    ),
                    P(
                        f"Export actual configuration to 'eos_config_{export_to_file_next_tag}.json' on '{eosdash_hostname}'."
                    ),
                ),
            ),
            Card(
                Details(
                    Summary(
                        Grid(
                            DivHStacked(
                                UkIcon(icon="play"),
                                AdminButton(
                                    "Import from file",
                                    hx_post="/eosdash/admin",
                                    hx_target="#page-content",
                                    hx_swap="innerHTML",
                                    hx_vals='js:{ "category": "configuration", "action": "import_from_file", "import_file_name": document.querySelector("[name=\'selected_import_file_name\']").value }',
                                ),
                                Select(
                                    *Options(*import_from_file_names),
                                    id="import_file_name",
                                    name="selected_import_file_name",  # Name of hidden input field with selected value
                                    placeholder="Select file",
                                ),
                            ),
                            import_from_file_status,
                        ),
                        cls="list-none",
                    ),
                    P(f"Import configuration from config file on '{eosdash_hostname}'."),
                ),
            ),
        ],
    )


def Admin(eos_host: str, eos_port: Union[str, int], data: Optional[dict] = None) -> Div:
    """Generates the administrative dashboard layout.

    This includes configuration management and other administrative tools.

    Args:
        eos_host (str): The hostname of the EOS server.
        eos_port (Union[str, int]): The port of the EOS server.
        data (Optional[dict], optional): Incoming data to trigger admin actions. Defaults to None.

    Returns:
        Div: A `Div` component containing the assembled admin interface.
    """
    # Get current configuration from server
    server = f"http://{eos_host}:{eos_port}"
    try:
        result = requests.get(f"{server}/v1/config")
        result.raise_for_status()
        config = result.json()
    except requests.exceptions.HTTPError as e:
        config = {}
        detail = result.json()["detail"]
        warning_msg = f"Can not retrieve configuration from {server}: {e}, {detail}"
        logger.warning(warning_msg)
        return Error(warning_msg)
    except Exception as e:
        warning_msg = f"Can not retrieve configuration from {server}: {e}"
        logger.warning(warning_msg)
        return Error(warning_msg)

    rows = []
    last_category = ""
    for category, admin in [
        AdminConfig(eos_host, eos_port, data, config),
    ]:
        if category != last_category:
            rows.append(H3(category))
            rows.append(DividerLine())
            last_category = category
        if isinstance(admin, list):
            for card in admin:
                rows.append(card)
        else:
            rows.append(admin)

    return Div(*rows, cls="space-y-4")
