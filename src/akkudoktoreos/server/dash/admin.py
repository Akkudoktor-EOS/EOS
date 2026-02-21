"""Admin UI components for EOS Dashboard.

This module provides functions to generate administrative UI components
for the EOS dashboard.
"""

import json
from typing import Any, Optional, Union

import requests
from fasthtml.common import Select
from loguru import logger
from monsterui.franken import (  # Select, TODO: Select from FrankenUI does not work - using Select from FastHTML instead
    H3,
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

from akkudoktoreos.server.dash.components import ConfigButton, Error, Success
from akkudoktoreos.server.dash.configuration import get_nested_value
from akkudoktoreos.server.dash.context import export_import_directory, request_url_for
from akkudoktoreos.utils.datetimeutil import to_datetime


def AdminCache(
    eos_host: str, eos_port: Union[str, int], data: Optional[dict], config: Optional[dict[str, Any]]
) -> tuple[str, Union[Card, list[Card]]]:
    """Creates a cache management card.

    Args:
        eos_host (str): The hostname of the EOS server.
        eos_port (Union[str, int]): The port of the EOS server.
        data (Optional[dict]): Incoming data containing action and category for processing.

    Returns:
        tuple[str, Union[Card, list[Card]]]: A tuple containing the cache category label and the `Card` UI component.
    """
    server = f"http://{eos_host}:{eos_port}"
    eos_hostname = "EOS server"
    eosdash_hostname = "EOSdash server"

    category = "cache"

    if data and data.get("category", None) == category:
        # This data is for us
        if data["action"] == "clear":
            # Clear all cache files
            try:
                result = requests.post(f"{server}/v1/admin/cache/clear", timeout=10)
                result.raise_for_status()
                status = Success(f"Cleared all cache files on '{eos_hostname}'")
            except requests.exceptions.HTTPError as e:
                detail = result.json()["detail"]
                status = Error(f"Can not clear all cache files on '{eos_hostname}': {e}, {detail}")
            except Exception as e:
                status = Error(f"Can not clear all cache files on '{eos_hostname}': {e}")
        elif data["action"] == "clear-expired":
            # Clear expired cache files
            try:
                result = requests.post(f"{server}/v1/admin/cache/clear-expired", timeout=10)
                result.raise_for_status()
                status = Success(f"Cleared expired cache files on '{eos_hostname}'")
            except requests.exceptions.HTTPError as e:
                detail = result.json()["detail"]
                status = Error(
                    f"Can not clear expired cache files on '{eos_hostname}': {e}, {detail}"
                )
            except Exception as e:
                status = Error(f"Can not clear expired cache files on '{eos_hostname}': {e}")

    return (
        category,
        [
            Card(
                Details(
                    Summary(
                        Grid(
                            DivHStacked(
                                UkIcon(icon="play"),
                                ConfigButton(
                                    "Clear all",
                                    hx_post=request_url_for("/eosdash/admin"),
                                    hx_target="#page-content",
                                    hx_swap="innerHTML",
                                    hx_vals='{"category": "cache", "action": "clear"}',
                                ),
                                P(f"cache files on '{eos_hostname}'"),
                            ),
                        ),
                        cls="list-none",
                    ),
                    P(f"Clear all cache files on '{eos_hostname}'."),
                ),
            ),
            Card(
                Details(
                    Summary(
                        Grid(
                            DivHStacked(
                                UkIcon(icon="play"),
                                ConfigButton(
                                    "Clear expired",
                                    hx_post=request_url_for("/eosdash/admin"),
                                    hx_target="#page-content",
                                    hx_swap="innerHTML",
                                    hx_vals='{"category": "cache", "action": "clear-expired"}',
                                ),
                                P(f"cache files on '{eos_hostname}'"),
                            ),
                        ),
                        cls="list-none",
                    ),
                    P(f"Clear expired cache files on '{eos_hostname}'."),
                ),
            ),
        ],
    )


def AdminConfig(
    eos_host: str,
    eos_port: Union[str, int],
    data: Optional[dict],
    config: Optional[dict[str, Any]],
    config_backup: Optional[dict[str, dict[str, Any]]],
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
    except Exception as e:
        logger.debug(f"general.config_file_path: {e}")
    # revert to backup
    revert_to_backup_status = (None,)
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
                result = requests.put(f"{server}/v1/config/file", timeout=10)
                result.raise_for_status()
                config_file_path = result.json()["general"]["config_file_path"]
                status = Success(f"Saved configuration to '{config_file_path}' on '{eos_hostname}'")
            except requests.exceptions.HTTPError as e:
                detail = result.json()["detail"]
                status = Error(
                    f"Can not save actual config to file on '{eos_hostname}': {e}, {detail}"
                )
            except Exception as e:
                status = Error(f"Can not save actual config to file on '{eos_hostname}': {e}")
        elif data["action"] == "revert_to_backup":
            # Revert configuration to backup file
            metadata = data.get("backup_metadata", None)
            if metadata and config_backup:
                date_time = metadata.split(" ")[0]
                backup_id = None
                for bkup_id, bkup_meta in config_backup.items():
                    if bkup_meta.get("date_time") == date_time:
                        backup_id = bkup_id
                        break
                if backup_id:
                    try:
                        result = requests.put(
                            f"{server}/v1/config/revert",
                            params={"backup_id": backup_id},
                            timeout=10,
                        )
                        result.raise_for_status()
                        config_file_path = result.json()["general"]["config_file_path"]
                        revert_to_backup_status = Success(
                            f"Reverted configuration to backup `{backup_id}` on '{eos_hostname}'"
                        )
                    except requests.exceptions.HTTPError as e:
                        detail = result.json()["detail"]
                        revert_to_backup_status = Error(
                            f"Can not revert to backup `{backup_id}` on '{eos_hostname}': {e}, {detail}"
                        )
                    except Exception as e:
                        revert_to_backup_status = Error(
                            f"Can not revert to backup `{backup_id}` on '{eos_hostname}': {e}"
                        )
                else:
                    revert_to_backup_status = Error(
                        f"Can not revert to backup `{backup_id}` on '{eos_hostname}': Invalid backup datetime {date_time}"
                    )
            else:
                revert_to_backup_status = Error(
                    f"Can not revert to backup configuration on '{eos_hostname}': No backup selected"
                )
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
                    result = requests.put(f"{server}/v1/config", json=import_config, timeout=10)
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
    import_from_file_names = sorted([f.name for f in list(export_import_directory.glob("*.json"))])
    if config_backup is None:
        revert_to_backup_metadata_list = ["Backup list not available"]
    else:
        revert_to_backup_metadata_list = sorted(
            [
                f"{backup_meta['date_time']} {backup_meta['version']}"
                for backup_id, backup_meta in config_backup.items()
            ]
        )

    return (
        category,
        [
            Card(
                Details(
                    Summary(
                        Grid(
                            DivHStacked(
                                UkIcon(icon="play"),
                                ConfigButton(
                                    "Save to file",
                                    hx_post=request_url_for("/eosdash/admin"),
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
                                ConfigButton(
                                    "Revert to backup",
                                    hx_post=request_url_for("/eosdash/admin"),
                                    hx_target="#page-content",
                                    hx_swap="innerHTML",
                                    hx_vals='js:{ "category": "configuration", "action": "revert_to_backup", "backup_metadata": document.querySelector("[name=\'selected_backup_metadata\']").value }',
                                ),
                                Select(
                                    *Options(*revert_to_backup_metadata_list),
                                    id="backup_metadata",
                                    name="selected_backup_metadata",  # Name of hidden input field with selected value
                                    cls="border rounded px-3 py-2 mr-2",
                                    placeholder="Select backup",
                                ),
                            ),
                            revert_to_backup_status,
                        ),
                        cls="list-none",
                    ),
                    P(f"Revert configuration to backup on '{eosdash_hostname}'."),
                ),
            ),
            Card(
                Details(
                    Summary(
                        Grid(
                            DivHStacked(
                                UkIcon(icon="play"),
                                ConfigButton(
                                    "Export to file",
                                    hx_post=request_url_for("/eosdash/admin"),
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
                                ConfigButton(
                                    "Import from file",
                                    hx_post=request_url_for("/eosdash/admin"),
                                    hx_target="#page-content",
                                    hx_swap="innerHTML",
                                    hx_vals='js:{ "category": "configuration", "action": "import_from_file", "import_file_name": document.querySelector("[name=\'selected_import_file_name\']").value }',
                                ),
                                Select(
                                    *Options(*import_from_file_names),
                                    id="import_file_name",
                                    name="selected_import_file_name",  # Name of hidden input field with selected value
                                    cls="border rounded px-3 py-2 mr-2",
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


def AdminDatabase(
    eos_host: str, eos_port: Union[str, int], data: Optional[dict], config: Optional[dict[str, Any]]
) -> tuple[str, Union[Card, list[Card]]]:
    """Creates a cache management card.

    Args:
        eos_host (str): The hostname of the EOS server.
        eos_port (Union[str, int]): The port of the EOS server.
        data (Optional[dict]): Incoming data containing action and category for processing.

    Returns:
        tuple[str, Union[Card, list[Card]]]: A tuple containing the cache category label and the `Card` UI component.
    """
    server = f"http://{eos_host}:{eos_port}"
    eos_hostname = "EOS server"
    eosdash_hostname = "EOSdash server"

    category = "database"

    status_vacuum = (None,)
    if data and data.get("category", None) == category:
        # This data is for us
        if data["action"] == "vacuum":
            # Remove old records from database
            try:
                result = requests.post(f"{server}/v1/admin/database/vacuum", timeout=30)
                result.raise_for_status()
                status_vacuum = Success(
                    f"Removed old data records from database on '{eos_hostname}'"
                )
            except requests.exceptions.HTTPError as e:
                detail = result.json()["detail"]
                status_vacuum = Error(
                    f"Can not remove old data records from database on '{eos_hostname}': {e}, {detail}"
                )
            except Exception as e:
                status_vacuum = Error(
                    f"Can not remove old data records from database on '{eos_hostname}': {e}"
                )

    return (
        category,
        [
            Card(
                Details(
                    Summary(
                        Grid(
                            DivHStacked(
                                UkIcon(icon="play"),
                                ConfigButton(
                                    "Vacuum",
                                    hx_post=request_url_for("/eosdash/admin"),
                                    hx_target="#page-content",
                                    hx_swap="innerHTML",
                                    hx_vals='{"category": "database", "action": "vacuum"}',
                                ),
                                P(f"Remove old data records from database on '{eos_hostname}'"),
                            ),
                            status_vacuum,
                        ),
                        cls="list-none",
                    ),
                    P(f"Remove old data records from database on '{eos_hostname}'."),
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
        result = requests.get(f"{server}/v1/config", timeout=10)
        result.raise_for_status()
        config = result.json()
    except requests.exceptions.HTTPError as e:
        detail = result.json()["detail"]
        warning_msg = f"Can not retrieve configuration from {server}: {e}, {detail}"
        logger.warning(warning_msg)
        return Error(warning_msg)
    except Exception as e:
        warning_msg = f"Can not retrieve configuration from {server}: {e}"
        logger.warning(warning_msg)
        return Error(warning_msg)
    # Get current configuration backups from server
    try:
        result = requests.get(f"{server}/v1/config/backup", timeout=10)
        result.raise_for_status()
        config_backup = result.json()
    except requests.exceptions.HTTPError as e:
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
        AdminCache(eos_host, eos_port, data, config),
        AdminConfig(eos_host, eos_port, data, config, config_backup),
        AdminDatabase(eos_host, eos_port, data, config),
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
