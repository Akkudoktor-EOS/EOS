from typing import Any, Callable, Optional

from fasthtml.common import H1, Div, P
from shad4fast.components.button import Button
from shad4fast.components.nav import Nav, NavItem, NavMenu

# Constants for styling
BG_COLOR: str = "lime-100"
BORDER_COLOR: str = "black-100"
PADDING: str = "4"


def with_card(
    color: str = BG_COLOR, border_color: str = BORDER_COLOR, padding: str = PADDING
) -> Callable:
    """A decorator to wrap content within a styled card container.

    Args:
        color (str): Background color of the card. Defaults to `BG_COLOR`.
        border_color (str): Border color of the card. Defaults to `BORDER_COLOR`.
        padding (str): Padding value for the card. Defaults to `PADDING`.

    Returns:
        Callable: The wrapped function that generates the card.
    """

    def decorator(func: Callable) -> Callable:
        def wrapper(*args: Any, **kwargs: Any) -> Div:
            content = func(*args, **kwargs)
            tailwind_clses = (
                f"p-{padding} rounded-lg shadow-lg bg-{color} border border-{border_color}"
            )
            return Div(
                Div(
                    content,
                    cls=tailwind_clses,
                ),
                cls="card border shadow-lg rounded-lg m-4",
            )

        return wrapper

    return decorator


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


def Body(content: str) -> Div:
    """Creates a styled body section with content.

    Args:
        content (str): The content to display in the body.

    Returns:
        Div: A styled `Div` element containing the body content.
    """
    return Div(P(content, cls="text-lg"), cls="body mb-4")


@with_card()
def Footer(info: str) -> Div:
    """Creates a styled footer with the provided information.

    Args:
        info (str): Footer information text.

    Returns:
        Div: A styled `Div` element containing the footer.
    """
    return Div(P(info, cls="text-sm font-medium"), cls="footer")


def NavMenuTrigger(*c: Any, cls: Optional[str] = None, **kwargs: Any) -> Button:
    """Creates a styled button for the navigation menu trigger.

    Args:
        *c: Positional arguments to pass to the button.
        cls (Optional[str]): Additional CSS classes for styling. Defaults to None.
        **kwargs: Additional keyword arguments for the button.

    Returns:
        Button: A styled `Button` component.
    """
    new_cls = "flex items-center space-x-1 text-sm font-bold"
    if cls:
        new_cls += f" {cls}"
    kwargs["cls"] = new_cls
    return Button(*c, variant="outline", size="lg", **kwargs)


@with_card()
def NavigationMenu(navigation_items: dict[str, str]) -> Nav:
    """Creates a navigation menu with dynamic menu items.

    Args:
        navigation_items (dict[str, str]): A dictionary of menu items where keys are menu names
            and values are paths for navigation.

    Returns:
        Nav: A styled `Nav` component containing the menu.
    """
    nav_items = [
        NavItem(
            NavMenuTrigger(
                menu,
                hx_get=f"{path}",
                hx_target="#page-content",
                hx_swap="innerHTML",
            ),
        )
        for menu, path in navigation_items.items()
    ]
    return Nav(NavMenu(*nav_items), cls="navigation")


@with_card(color="white")
def Content(content: Any) -> Div:
    """Creates a content section within a styled card.

    Args:
        content (Any): The content to display.

    Returns:
        Div: A styled `Div` element containing the content.
    """
    return Div(content, id="page-content")


def Page(
    title: Optional[str], navigation_items: dict[str, str], content: Any, footer_info: str
) -> Div:
    """Generates a full-page layout with a header, navigation menu, content, and footer.

    Args:
        title (Optional[str]): The page title.
        navigation_items (dict[str, str]): A dictionary of navigation menu items.
        content (Any): The main content for the page.
        footer_info (str): Footer information text.

    Returns:
        Div: A `Div` element representing the entire page layout.
    """
    return Div(
        Header(title),
        Body(
            Div(
                NavigationMenu(navigation_items),
                Content(content),
            ),
        ),
        Footer(footer_info),
        cls="w-screen p-6 mx-auto shadow-lg rounded-lg",
    )
