import json
from typing import Any, Callable, Optional, Union

from fasthtml.common import H1, Button, Div, Li, Select
from monsterui.daisy import (
    Alert,
    AlertT,
)
from monsterui.foundations import stringify
from monsterui.franken import (  # Select: Does not work - using Select from FastHTML instead;; Button: Does not pass hx_vals - using Button from FastHTML instead
    H3,
    ButtonT,
    Card,
    Code,
    Container,
    ContainerT,
    Details,
    DivHStacked,
    DivLAligned,
    DivRAligned,
    Form,
    Grid,
    Input,
    Option,
    P,
    Pre,
    Summary,
    TabContainer,
    UkIcon,
)

from akkudoktoreos.server.dash.context import request_url_for

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


def JsonView(data: Any) -> Pre:
    """Render structured data as formatted JSON inside a styled <pre> block.

    The data is serialized to JSON using indentation for readability and
    UTF-8 characters are preserved. The JSON is wrapped in a <code> element
    with a JSON language class to support syntax highlighting, and then
    placed inside a <pre> container with MonsterUI-compatible styling.

    The JSON output is height-constrained and scrollable to safely display
    large payloads without breaking the page layout.

    Args:
        data: Any JSON-serializable Python object to render.

    Returns:
        A FastHTML `Pre` element containing a formatted JSON representation
        of the input data.
    """
    code_str = json.dumps(data, indent=2, ensure_ascii=False)
    return Pre(
        Code(code_str, cls="language-json"),
        cls="rounded-lg bg-muted p-3 max-h-[30vh] overflow-y-auto overflow-x-hidden whitespace-pre-wrap",
    )


def TextView(*c: Any, cls: Optional[Union[str, tuple]] = None, **kwargs: Any) -> Pre:
    """Render plain text with preserved line breaks and wrapped long lines.

    This view uses a <pre> element with whitespace wrapping enabled so that
    newline characters are respected while long lines are wrapped instead
    of causing horizontal scrolling.

    Args:
        *c (Any): Positional arguments representing the TextView content.
        cls (Optional[Union[str, tuple]]): Additional CSS classes for styling. Defaults to None.
        **kwargs (Any): Additional keyword arguments passed to the `Pre`.

    Returns:
        A FastHTML `Pre` element that displays the text with preserved
        formatting and line wrapping.
    """
    new_cls = "whitespace-pre-wrap"
    if cls:
        new_cls += f"{stringify(cls)}"
    kwargs["cls"] = new_cls
    return Pre(*c, **kwargs)


def Success(*c: Any) -> Alert:
    return Alert(
        DivLAligned(
            UkIcon("check"),
            TextView(*c),
        ),
        cls=AlertT.success,
    )


def Error(*c: Any) -> Alert:
    return Alert(
        DivLAligned(
            UkIcon("triangle-alert"),
            TextView(*c),
        ),
        cls=AlertT.error,
    )


def ConfigButton(*c: Any, cls: Optional[Union[str, tuple]] = None, **kwargs: Any) -> Button:
    """Creates a styled button for configuration actions.

    Args:
        *c (Any): Positional arguments representing the button's content.
        cls (Optional[Union[str, tuple]]): Additional CSS classes for styling. Defaults to None.
        **kwargs (Any): Additional keyword arguments passed to the `Button`.

    Returns:
        Button: A styled `Button` component for configuration actions.
    """
    new_cls = f"px-4 py-2 rounded {ButtonT.primary}"
    if cls:
        new_cls += f"{stringify(cls)}"
    kwargs["cls"] = new_cls
    return Button(*c, submit=False, **kwargs)


def make_config_update_form() -> Callable[[str, str], Grid]:
    """Factory for a form that sets a single configuration value.

    Returns:
        A function (config_name: str, value: str) -> Grid
    """

    def ConfigUpdateForm(config_name: str, value: str) -> Grid:
        config_id = config_name.lower().replace(".", "-")

        return Grid(
            DivRAligned(P("update")),
            Grid(
                Form(
                    Input(value="update", type="hidden", id="action"),
                    Input(value=config_name, type="hidden", id="key"),
                    Input(value=value, type="text", id="value"),
                    hx_put=request_url_for("/eosdash/configuration"),
                    hx_target="#page-content",
                    hx_swap="innerHTML",
                ),
            ),
            id=f"{config_id}-update-form",
        )

    return ConfigUpdateForm


def make_config_update_value_form(
    available_values: list[str],
) -> Callable[[str, str], Grid]:
    """Factory for a form that sets a single configuration value with pre-set avaliable values.

    Args:
        available_values: Allowed values for the configuration

    Returns:
        A function (config_name: str, value: str) -> Grid
    """

    def ConfigUpdateValueForm(config_name: str, value: str) -> Grid:
        config_id = config_name.lower().replace(".", "-")

        return Grid(
            DivRAligned(P("update value")),
            DivHStacked(
                ConfigButton(
                    "Set",
                    hx_put=request_url_for("/eosdash/configuration"),
                    hx_target="#page-content",
                    hx_swap="innerHTML",
                    hx_vals=f"""js:{{
                        action: "update",
                        key: "{config_name}",
                        value: document
                            .querySelector("[name='{config_id}_selected_value']")
                            .value
                    }}""",
                ),
                Select(
                    Option("Select a value...", value="", selected=True, disabled=True),
                    *[
                        Option(
                            val,
                            value=val,
                            selected=(val == value),
                        )
                        for val in available_values
                    ],
                    id=f"{config_id}-value-select",
                    name=f"{config_id}_selected_value",
                    required=True,
                    cls="border rounded px-3 py-2 mr-2 col-span-4",
                ),
            ),
            id=f"{config_id}-update-value-form",
        )

    return ConfigUpdateValueForm


def make_config_update_list_form(available_values: list[str]) -> Callable[[str, str], Grid]:
    """Factory function that creates a ConfigUpdateListForm with pre-set available values.

    Args:
        available_values: List of available values to choose from

    Returns:
        A function that creates ConfigUpdateListForm instances with the given available_values.
        The returned function takes (config_name: str, value: str) and returns a Grid.
    """

    def ConfigUpdateListForm(config_name: str, value: str) -> Grid:
        """Creates a card with a form to add/remove values from a list.

        Sends to "/eosdash/configuration":
            The form sends an HTTP PUT request with the following parameters:

            - key (str): The configuration key name (value of config_name parameter)
            - value (str): A JSON string representing the updated list of values

            The value parameter will always be a valid JSON string representation of a list.

        Args:
            config_name: The name of the configuration
            value (str): The current value of the configuration, a list of values in json format.
        """
        current_values = json.loads(value)
        if current_values is None:
            current_values = []
        config_id = config_name.lower().replace(".", "-")

        return Grid(
            DivRAligned(P("update list")),
            Grid(
                # Form to add new value to list
                DivHStacked(
                    ConfigButton(
                        "Add",
                        hx_put=request_url_for("/eosdash/configuration"),
                        hx_target="#page-content",
                        hx_swap="innerHTML",
                        hx_vals=f"""js:{{
                            action: "update",
                            key: "{config_name}",
                            value: JSON.stringify(
                                [...new Set([
                                    ...{json.dumps(current_values)},
                                    document.querySelector("[name='{config_id}_selected_add_value']").value.trim()
                                ])].filter(v => v !== "")
                            )
                        }}""",
                    ),
                    Select(
                        Option("Select a value...", value="", selected=True, disabled=True),
                        *[
                            Option(val, value=val, disabled=val in current_values)
                            for val in available_values
                        ],
                        id=f"{config_id}-add-value-select",
                        name=f"{config_id}_selected_add_value",  # Name of hidden input with selected value
                        required=True,
                        cls="border rounded px-3 py-2 mr-2 col-span-4",
                    ),
                ),
                # Form to delete value from list
                DivHStacked(
                    ConfigButton(
                        "Delete",
                        hx_put=request_url_for("/eosdash/configuration"),
                        hx_target="#page-content",
                        hx_swap="innerHTML",
                        hx_vals=f"""js:{{
                            action: "update",
                            key: "{config_name}",
                            value: JSON.stringify(
                                [...new Set([
                                    ...{json.dumps(current_values)}
                                ])].filter(v => v !== document.querySelector("[name='{config_id}_selected_delete_value']").value.trim())
                            )
                        }}""",
                    ),
                    Select(
                        Option("Select a value...", value="", selected=True, disabled=True),
                        *[Option(val, value=val) for val in current_values],
                        id=f"{config_id}-delete-value-select",
                        name=f"{config_id}_selected_delete_value",  # Name of hidden input with selected value
                        required=True,
                        cls="border rounded px-3 py-2 mr-2 col-span-4",
                    ),
                ),
                cols=1,
            ),
            id=f"{config_id}-update-list-form",
        )

    # Return the function that creates a ConfigUpdateListForm instance
    return ConfigUpdateListForm


def make_config_update_map_form(
    available_keys: list[str] | None = None,
    available_values: list[str] | None = None,
) -> Callable[[str, str], Grid]:
    """Factory function that creates a ConfigUpdateMapForm.

    Args:
        available_keys: Optional list of allowed keys (None = free text)
        available_values: Optional list of allowed values (None = free text)

    Returns:
        A function that creates ConfigUpdateMapForm instances.
        The returned function takes (config_name: str, value: str) and returns a Grid.
    """

    def ConfigUpdateMapForm(config_name: str, value: str) -> Grid:
        """Creates a card with a form to add/update/delete entries in a map."""
        current_map: dict[str, str] = json.loads(value) or {}
        config_id = config_name.lower().replace(".", "-")

        return Grid(
            DivRAligned(P("update map")),
            Grid(
                # Add / update key-value pair
                DivHStacked(
                    ConfigButton(
                        "Set",
                        hx_put=request_url_for("/eosdash/configuration"),
                        hx_target="#page-content",
                        hx_swap="innerHTML",
                        hx_vals=f"""js:{{
                            action: "update",
                            key: "{config_name}",
                            value: JSON.stringify(
                                Object.assign(
                                    {json.dumps(current_map)},
                                    {{
                                        [document.querySelector("[name='{config_id}_set_key']").value.trim()]:
                                        document.querySelector("[name='{config_id}_set_value']").value.trim()
                                    }}
                                )
                            )
                        }}""",
                    ),
                    (
                        Select(
                            Option("Select key...", value="", selected=True, disabled=True),
                            *[Option(k, value=k) for k in (sorted(available_keys) or [])],
                            name=f"{config_id}_set_key",
                            cls="border rounded px-3 py-2 col-span-2",
                        )
                        if available_keys
                        else Input(
                            name=f"{config_id}_set_key",
                            placeholder="Key",
                            required=True,
                            cls="border rounded px-3 py-2 col-span-2",
                        ),
                    ),
                    (
                        Select(
                            Option("Select value...", value="", selected=True, disabled=True),
                            *[Option(k, value=k) for k in (sorted(available_values) or [])],
                            name=f"{config_id}_set_value",
                            cls="border rounded px-3 py-2 col-span-2",
                        )
                        if available_values
                        else Input(
                            name=f"{config_id}_set_value",
                            placeholder="Value",
                            required=True,
                            cls="border rounded px-3 py-2 col-span-2",
                        ),
                    ),
                ),
                # Delete key
                DivHStacked(
                    ConfigButton(
                        "Delete",
                        hx_put=request_url_for("/eosdash/configuration"),
                        hx_target="#page-content",
                        hx_swap="innerHTML",
                        hx_vals=f"""js:{{
                            action: "update",
                            key: "{config_name}",
                            value: JSON.stringify(
                                Object.fromEntries(
                                    Object.entries({json.dumps(current_map)})
                                        .filter(([k]) =>
                                            k !== document.querySelector("[name='{config_id}_delete_key']").value
                                        )
                                )
                            )
                        }}""",
                    ),
                    Select(
                        Option("Select key...", value="", selected=True, disabled=True),
                        *[Option(k, value=k) for k in sorted(current_map.keys())],
                        name=f"{config_id}_delete_key",
                        required=True,
                        cls="border rounded px-3 py-2 col-span-4",
                    ),
                ),
                cols=1,
            ),
            id=f"{config_id}-update-map-form",
        )

    return ConfigUpdateMapForm


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
    update_form_factory: Optional[Callable[[str, str], Grid]] = None,
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
        update_form_factory (Optional[Callable[[str, str], Grid]]): The factory to create a form to
            use to update the configuration value. Defaults to simple text input.

    Returns:
        Card: A styled Card component containing the configuration details.
    """
    config_id = config_name.replace(".", "-")
    if not update_value:
        update_value = value
    if not update_open:
        update_open = False
    if not update_form_factory:
        # Default update form
        update_form = make_config_update_form()(config_name, update_value)
    else:
        update_form = update_form_factory(config_name, update_value)
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
                    JsonView(json.loads(value)),
                ),
                cls="list-none",
            ),
            Grid(
                TextView(description),
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
            update_form if read_only == "rw" and not deprecated else None,
            # Last error
            Grid(
                DivRAligned(P("update error")),
                TextView(update_error),
            )
            if update_error
            else None,
            # Provide minimal update form on error if complex update_form is used
            make_config_update_form()(config_name, update_value)
            if update_error and update_form_factory is not None
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
        hx_get=request_url_for(path),
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
                hx_get=request_url_for(path),
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
