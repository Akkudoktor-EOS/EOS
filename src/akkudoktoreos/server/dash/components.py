from typing import Any, Optional, Union

from fasthtml.common import FT, H1, Div, Li, P
from monsterui.foundations import stringify
from monsterui.franken import (
    Button,
    ButtonT,
    Card,
    Container,
    ContainerT,
    TabContainer,
    render_md,
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


def Markdown(md: str) -> FT:
    return render_md(md)


def Header(title: Optional[str]) -> Div:
    """Creates a styled header with a title.

    Args:
        title (Optional[str]): The title text for the header.

    Returns:
        Div: A styled `Div` element containing the header.
    """
    if title is None:
        return Div("", cls="header")
    return Div(H1(title, cls="text-2xl font-bold mb-4"), cls="header")


def Footer(info: str) -> Card:
    """Creates a styled footer with the provided information.

    Args:
        info (str): Footer information text.

    Returns:
        Card: A styled `Card` element containing the footer.
    """
    return Card(Container(P(info, cls="text-sm font-medium"), cls="footer"))


def DashboardTrigger(*c: Any, cls: Optional[Union[str, tuple]] = None, **kwargs: Any) -> Button:
    """Creates a styled button for the dashboard trigger.

    Args:
        *c: Positional arguments to pass to the button.
        cls (Optional[str]): Additional CSS classes for styling. Defaults to None.
        **kwargs: Additional keyword arguments for the button.

    Returns:
        Button: A styled `Button` component.
    """
    new_cls = f"{ButtonT.primary}"
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
                menu,
                hx_get=f"{path}",
                hx_target="#page-content",
                hx_swap="innerHTML",
            ),
        )
        for menu, path in dashboard_items.items()
    ]
    return Card(TabContainer(*dash_items), alt=True)


def Content(content: Any) -> Card:
    """Creates a content section within a styled card.

    Args:
        content (Any): The content to display.

    Returns:
        Card: A styled `Card` element containing the content.
    """
    return Card(Container(content, id="page-content"))


def Page(
    title: Optional[str], dashboard_items: dict[str, str], content: Any, footer_info: str
) -> Div:
    """Generates a full-page layout with a header, dashboard items, content, and footer.

    Args:
        title (Optional[str]): The page title.
        dashboard_items (dict[str, str]): A dictionary of dashboard items.
        content (Any): The main content for the page.
        footer_info (str): Footer information text.

    Returns:
        Div: A `Div` element representing the entire page layout.
    """
    return Container(
        Header(title),
        DashboardTabs(dashboard_items),
        Content(content),
        Footer(footer_info),
        cls=("w-screen p-4 space-y-4", ContainerT.xl),
    )
