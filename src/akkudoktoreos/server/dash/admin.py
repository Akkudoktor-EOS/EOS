"""Admin UI components for EOS Dashboard.

This module provides functions to generate administrative UI components
for the EOS dashboard.
"""

from typing import Any, Optional, Union

import requests
from fasthtml.common import Div
from monsterui.foundations import stringify
from monsterui.franken import (
    Button,
    ButtonT,
    Card,
    Details,
    DivHStacked,
    DividerLine,
    Grid,
    P,
    Summary,
    UkIcon,
)


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


def AdminConfig(eos_host: str, eos_port: Union[str, int], data: Optional[dict]) -> Card:
    """Creates a configuration management card with save-to-file functionality.

    Args:
        eos_host (str): The hostname of the EOS server.
        eos_port (Union[str, int]): The port of the EOS server.
        data (Optional[dict]): Incoming data containing action and category for processing.

    Returns:
        tuple[str, Card]: A tuple containing the configuration category label and the `Card` UI component.
    """
    server = f"http://{eos_host}:{eos_port}"

    category = "configuration"
    status = (None,)
    if data and data["category"] == category:
        # This data is for us
        if data["action"] == "save_to_file":
            # Safe current configuration to file
            try:
                result = requests.put(f"{server}/v1/config/file")
                result.raise_for_status()
                config_file_path = result.json()["general"]["config_file_path"]
                status = P(
                    f"Actual config saved to {config_file_path} on {server}",
                    cls="text-left",
                )
            except requests.exceptions.HTTPError as err:
                detail = result.json()["detail"]
                status = P(
                    f"Can not save actual config to file on {server}: {err}, {detail}",
                    cls="text-left",
                )
    return (
        category,
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
                        ),
                        status,
                    ),
                    cls="list-none",
                ),
                P(f"Safe actual configuration to config file on {server}."),
            ),
        ),
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
    rows = []
    last_category = ""
    for category, admin in [
        AdminConfig(eos_host, eos_port, data),
    ]:
        if category != last_category:
            rows.append(P(category))
            rows.append(DividerLine())
            last_category = category
        rows.append(admin)

    return Div(*rows, cls="space-y-4")
