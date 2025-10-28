from typing import Any, Optional, Union

from fasthtml.common import H1, Button, Div, Li
from monsterui.daisy import (
    Alert,
    AlertT,
)
from monsterui.foundations import stringify
from monsterui.franken import (  # Button, Does not pass hx_vals
    H3,
    Card,
    Container,
    ContainerT,
    Details,
    DivLAligned,
    DivRAligned,
    Form,
    Grid,
    Input,
    P,
    Summary,
    TabContainer,
    UkIcon,
)

scrollbar_viewport_styles = (
    "scrollbar-width: none; -ms-overflow-style: none; -webkit-overflow-scrolling: touch;"
)

scrollbar_cls = "flex touch-none select-none transition-colors p-[1px]"


def ScrollArea(
    *c: Any, cls: Optional[Union[str, tuple]] = None, orientation: str = "vertical", **kwargs: Any
) -> Div:
    """Creates a styled scroll area.

    Args:
        orientation (str):	The orientation of the scroll area. Defaults to vertical.
    """
    new_cls = "relative overflow-hidden"
    if cls:
        new_cls += f" {stringify(cls)}"
    kwargs["cls"] = new_cls

    content = Div(
        Div(*c, style="min-width:100%;display:table;"),
        style=f"overflow: {'hidden scroll' if orientation == 'vertical' else 'scroll'}; {scrollbar_viewport_styles}",
        cls="w-full h-full rounded-[inherit]",
        data_ref="viewport",
    )

    scrollbar = Div(
        Div(cls="bg-border rounded-full hidden relative flex-1", data_ref="thumb"),
        cls=f"{scrollbar_cls} flex-col h-2.5 w-full border-t border-t-transparent"
        if orientation == "horizontal"
        else f"{scrollbar_cls} w-2.5 h-full border-l border-l-transparent",
        data_ref="scrollbar",
        style=f"position: absolute;{'right:0; top:0;' if orientation == 'vertical' else 'bottom:0; left:0;'}",
    )

    return Div(
        content,
        scrollbar,
        role="region",
        tabindex="0",
        data_orientation=orientation,
        data_ref_scrollarea=True,
        aria_label="Scrollable content",
        **kwargs,
    )


def Success(*c: Any) -> Alert:
    return Alert(
        DivLAligned(
            UkIcon("check"),
            P(*c),
        ),
        cls=AlertT.success,
    )


def Error(*c: Any) -> Alert:
    return Alert(
        DivLAligned(
            UkIcon("triangle-alert"),
            P(*c),
        ),
        cls=AlertT.error,
    )


def ConfigCard(
    config_name: str,
    config_type: str,
    read_only: str,
    value: str,
    default: str,
    description: str,
    deprecated: Optional[Union[str, bool]],
    update_error: Optional[str],
    update_value: Optional[str],
    update_open: Optional[bool],
) -> Card:
    """Creates a styled configuration card for displaying configuration details.

    This function generates a configuration card that is displayed in the UI with
    various sections such as configuration name, type, description, default value,
    current value, and error details. It supports both read-only and editable modes.

    Args:
        config_name (str): The name of the configuration.
        config_type (str): The type of the configuration.
        read_only (str): Indicates if the configuration is read-only ("rw" for read-write,
                         any other value indicates read-only).
        value (str): The current value of the configuration.
        default (str): The default value of the configuration.
        description (str): A description of the configuration.
        deprecated (Optional[Union[str, bool]]): The deprecated marker of the configuration.
        update_error (Optional[str]): The error message, if any, during the update process.
        update_value (Optional[str]): The value to be updated, if different from the current value.
        update_open (Optional[bool]): A flag indicating whether the update section of the card
                                      should be initially expanded.

    Returns:
        Card: A styled Card component containing the configuration details.
    """
    config_id = config_name.replace(".", "-")
    if not update_value:
        update_value = value
    if not update_open:
        update_open = False
    if deprecated:
        if isinstance(deprecated, bool):
            deprecated = "Deprecated"
    return Card(
        Details(
            Summary(
                Grid(
                    Grid(
                        DivLAligned(
                            UkIcon(icon="play"),
                            P(config_name),
                        ),
                        DivRAligned(
                            P(read_only),
                        ),
                    ),
                    P(value),
                ),
                cls="list-none",
            ),
            Grid(
                P(description),
                P(config_type),
            )
            if not deprecated
            else None,
            Grid(
                P(deprecated),
                P("DEPRECATED!"),
            )
            if deprecated
            else None,
            # Default
            Grid(
                DivRAligned(P("default")),
                P(default),
            )
            if read_only == "rw" and not deprecated
            else None,
            # Set value
            Grid(
                DivRAligned(P("update")),
                Grid(
                    Form(
                        Input(value=config_name, type="hidden", id="key"),
                        Input(value=update_value, type="text", id="value"),
                        hx_put="/eosdash/configuration",
                        hx_target="#page-content",
                        hx_swap="innerHTML",
                    ),
                ),
            )
            if read_only == "rw" and not deprecated
            else None,
            # Last error
            Grid(
                DivRAligned(P("update error")),
                P(update_error),
            )
            if update_error
            else None,
            cls="space-y-4 gap-4",
            open=update_open,
        ),
        cls="w-full",
    )


def DashboardHeader(title: Optional[str]) -> Div:
    """Creates a styled header with a title.

    Args:
        title (Optional[str]): The title text for the header.

    Returns:
        Div: A styled `Div` element containing the header.
    """
    if title is None:
        return Div("", cls="header")
    return Div(H1(title, cls="text-2xl font-bold mb-4"), cls="header")


def DashboardFooter(*c: Any, path: str) -> Card:
    """Creates a styled footer with the provided information.

    The footer content is reloaded every 5 seconds from path.

    Args:
        path (str): Path to reload footer content from

    Returns:
        Card: A styled `Card` element containing the footer.
    """
    return Card(
        Container(*c, id="footer-content"),
        hx_get=f"{path}",
        hx_trigger="every 5s",
        hx_target="#footer-content",
        hx_swap="innerHTML",
    )


def DashboardTrigger(*c: Any, cls: Optional[Union[str, tuple]] = None, **kwargs: Any) -> Button:
    """Creates a styled button for the dashboard trigger.

    Args:
        *c: Positional arguments to pass to the button.
        cls (Optional[str]): Additional CSS classes for styling. Defaults to None.
        **kwargs: Additional keyword arguments for the button.

    Returns:
        Button: A styled `Button` component.
    """
    #   new_cls = f"{ButtonT.primary} uk-border-rounded uk-padding-small"
    new_cls = "uk-btn uk-btn-primary uk-border-rounded uk-padding-medium"
    if cls:
        new_cls += f" {stringify(cls)}"
    kwargs["cls"] = new_cls
    return Button(*c, submit=False, **kwargs)


def DashboardTabs(dashboard_items: dict[str, str]) -> Card:
    """Creates a dashboard tab with dynamic dashboard items.

    Args:
        dashboard_items (dict[str, str]): A dictionary of dashboard items where keys are item names
            and values are paths for navigation.

    Returns:
        Card: A styled `Card` component containing the dashboard tabs.
    """
    dash_items = [
        Li(
            DashboardTrigger(
                H3(menu),
                hx_get=f"{path}",
                hx_target="#page-content",
                hx_swap="innerHTML",
                hx_vals='js:{ "dark": window.matchMedia("(prefers-color-scheme: dark)").matches }',
            ),
        )
        for menu, path in dashboard_items.items()
    ]
    return Card(TabContainer(*dash_items, cls="gap-4"), alt=True)


def DashboardContent(content: Any) -> Card:
    """Creates a content section within a styled card.

    Args:
        content (Any): The content to display.

    Returns:
        Card: A styled `Card` element containing the content.
    """
    return Card(
        ScrollArea(Container(content, id="page-content"), cls="h-[75vh] w-full rounded-md"),
    )


def Page(
    title: Optional[str],
    dashboard_items: dict[str, str],
    content: Any,
    footer_content: Any,
    footer_path: str,
) -> Div:
    """Generates a full-page layout with a header, dashboard items, content, and footer.

    Args:
        title (Optional[str]): The page title.
        dashboard_items (dict[str, str]): A dictionary of dashboard items.
        content (Any): The main content for the page.
        footer_content (Any): Footer content.
        footer_path (Any): Path to reload footer content from.

    Returns:
        Div: A `Div` element representing the entire page layout.
    """
    return Container(
        DashboardHeader(title),
        DashboardTabs(dashboard_items),
        DashboardContent(content),
        DashboardFooter(footer_content, path=footer_path),
        cls=("bg-background text-foreground w-screen p-4 space-y-4", ContainerT.xl),
    )
