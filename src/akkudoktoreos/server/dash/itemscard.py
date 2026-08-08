"""Generic expandable list-of-sub-model configuration card for EOSdash.

This module provides `ConfigItemsCard`, a reusable FastHTML/MonsterUI
card component that renders any ``list[PydanticSubModel]`` config field as a
collapsible outer card containing one collapsible inner card per list item.

It is intentionally free of imports from ``configuration.py`` to avoid
circular dependencies.  The one runtime dependency on
``create_config_details`` is injected by the caller.

Typical usage in ``configuration.py``::

    from akkudoktoreos.server.dash.itemscard import ConfigItemsCard

    hint = UI_HINTS.get(config["name"])
    if hint and hint.form == "items" and not config["deprecated"]:
        rows.append(
            ConfigItemsCard(
                config=config,
                hint=hint,
                config_details=config_details,
                config_update_latest=config_update_latest,
                create_config_details=create_config_details,
            )
        )
"""

import json
from typing import Any, Callable, Optional

from loguru import logger
from monsterui.franken import (
    H4,
    Card,
    Details,
    Div,
    DivHStacked,
    DivLAligned,
    DivRAligned,
    Form,
    Grid,
    Input,
    Kbd,
    P,
    Summary,
    UkIcon,
)

from akkudoktoreos.server.dash.carditems import (
    item_model_defaults,
    required_field_inputs,
)
from akkudoktoreos.server.dash.components import (
    ConfigButton,
    ConfigCard,
    JsonView,
    UpdateError,
)
from akkudoktoreos.server.dash.context import request_url_for
from akkudoktoreos.server.dash.markdown import Markdown
from akkudoktoreos.server.dash.uihints import (
    UiHint,
    hint_for_indexed_field,
    resolve_form_factory,
    resolve_item_model,
)

# ---------------------------------------------------------------------------
# Internal helpers
# ---------------------------------------------------------------------------


def _add_control(
    config_name: str,
    item_model: Any,
    items_list: list,
    read_only: str,
) -> Any:
    """Build the 'Add item' control for the outer card header.

    When the item model can be fully defaulted, this is a one-click button
    that appends immediately. When required fields have no default (e.g.
    `consumption_wh`/`duration_h` on `HomeApplianceCommonSettings`), clicking
    would otherwise submit an invalid item, so instead this renders a small
    inline form that collects those values first and only builds the PUT
    payload once every required input is non-empty (enforced via HTML
    `required`).
    """
    if read_only != "rw":
        return None

    new_item_defaults, required_missing = item_model_defaults(item_model)

    if not required_missing:
        appended_json = json.dumps(json.dumps(items_list + [new_item_defaults]))
        return ConfigButton(
            UkIcon("plus"),
            " Add item",
            hx_put=request_url_for("/eosdash/configuration"),
            hx_target="#page-content",
            hx_swap="innerHTML",
            hx_vals=f'js:{{ action: "update", key: "{config_name}", value: {appended_json} }}',
            cls="ml-4 px-3 py-1 text-sm",
        )

    id_prefix = f"new-item-{config_name}"
    inputs, js_pairs = required_field_inputs(item_model, required_missing, id_prefix)

    defaults_json = json.dumps(new_item_defaults)
    items_json = json.dumps(items_list)
    build_value_expr = (
        "(function(){"
        f"var base = {defaults_json};"
        f"var extra = {{ {', '.join(js_pairs)} }};"
        f"var items = {items_json};"
        "return JSON.stringify(items.concat([Object.assign({}, base, extra)]));"
        "})()"
    )

    return Details(
        Summary(
            UkIcon("plus"),
            " Add item",
            cls="list-none cursor-pointer inline-flex items-center gap-1 ml-4",
        ),
        Form(
            *inputs,
            ConfigButton(
                "Create",
                hx_put=request_url_for("/eosdash/configuration"),
                hx_target="#page-content",
                hx_swap="innerHTML",
                hx_vals=f'js:{{ action: "update", key: "{config_name}", value: {build_value_expr} }}',
                cls="mt-2 px-3 py-1 text-sm",
            ),
            cls="absolute z-10 mt-1 p-3 rounded-md border bg-background shadow-md space-y-2",
        ),
        cls="relative",
    )


def _delete_control(config_name: str, items_list: list, index: int) -> Details:
    """Build the two-click delete control for a single inner item card header.

    The first click opens a ``<details>`` panel revealing a red "Confirm
    delete" button.  Clicking outside collapses it.  The second click
    (on the confirm button) submits an ``hx_put`` with the list minus the
    given index.

    Args:
        config_name: Dotted config key name, e.g. ``"pvforecast.planes"``.
        items_list: The current full list of item dicts.
        index: The zero-based index of the item to delete.

    Returns:
        A ``Details`` component implementing the two-click confirm pattern.
    """
    remaining_json = json.dumps(json.dumps([w for j, w in enumerate(items_list) if j != index]))
    return Details(
        Summary(
            UkIcon("trash-2", cls="text-muted-foreground hover:text-destructive cursor-pointer"),
            cls="list-none",
        ),
        Div(
            ConfigButton(
                UkIcon("trash-2"),
                " Confirm delete",
                hx_put=request_url_for("/eosdash/configuration"),
                hx_target="#page-content",
                hx_swap="innerHTML",
                hx_vals=f'js:{{ action: "update", key: "{config_name}", value: {remaining_json} }}',
                cls="px-3 py-1 text-sm bg-destructive text-destructive-foreground hover:bg-destructive/90",
            ),
            cls="absolute z-10 mt-1 p-2 rounded-md border bg-background shadow-md",
        ),
        cls="relative",
    )


def _inner_card(
    config_name: str,
    item_path: str,
    path_parts: list[str],
    index: int,
    item_value: str,
    is_empty: bool,
    read_only: str,
    item_rows: list,
    item_update_open: bool,
    delete_control: Optional[Details],
) -> Card:
    """Render a single collapsible inner card for one list item.

    Args:
        config_name: Dotted config key of the parent list field.
        item_path: Dotted path prefix for this item type, e.g.
            ``"pvforecast.planes"``.
        path_parts: ``item_path`` split on ``"."``.
        index: Zero-based position of this item in the list.
        item_value: JSON-encoded current value of this item.
        is_empty: ``True`` when the item dict is falsy (empty or ``None``).
        read_only: ``"rw"`` or ``"ro"`` inherited from the parent field.
        item_rows: Pre-built list of ``ConfigCard`` children for this item.
        item_update_open: Whether this card should start expanded.
        delete_control: The two-click delete ``Details`` widget, or ``None``
            for read-only fields.

    Returns:
        A ``Card`` component for this item slot.
    """
    return Card(
        Details(
            Summary(
                Grid(
                    Grid(
                        DivLAligned(
                            UkIcon(icon="play"),
                            H4(
                                f"{item_path}.{index}",
                                cls="text-muted-foreground" if is_empty else "",
                            ),
                            delete_control,
                        ),
                        DivRAligned(
                            P(
                                "empty" if is_empty else read_only,
                                cls="text-xs text-muted-foreground" if is_empty else "",
                            ),
                        ),
                    ),
                    JsonView(json.loads(item_value)),
                ),
                cls="list-none",
            ),
            *item_rows,
            cls="space-y-4 gap-4",
            open=item_update_open,
        ),
        cls=f"w-full {'opacity-60' if is_empty else ''}",
    )


def _outer_card(
    config_name: str,
    config_type: str,
    read_only: str,
    value: str,
    default: str,
    description: str,
    scope: Optional[list[str]],
    num_items: int,
    add_button: Any,
    items_update_value: str,
    items_update_error: Optional[str],
    items_update_open: bool,
    rows: list,
) -> Card:
    """Render the outer collapsible card for the whole list field.

    Args:
        config_name: Dotted config key name.
        config_type: Human-readable type string from config details.
        read_only: ``"rw"`` or ``"ro"``.
        value: JSON-encoded current list value.
        default: JSON-encoded default value.
        description: Field description text.
        num_items: Current number of items, shown as a badge.
        add_button: The "Add item" control from ``_add_control`` — a
            ``ConfigButton`` when the item model is fully defaulted, a
            ``Details``/``Form`` combo when required fields must be
            collected first, or ``None`` for read-only fields.
        items_update_value: Value to pre-fill the fallback text input.
        items_update_error: Error string from the last failed update, or
            ``None``.
        items_update_open: Whether the outer card starts expanded.
        rows: Pre-built list of inner ``Card`` components.

    Returns:
        The outer ``Card`` component.
    """
    return Card(
        Details(
            Summary(
                Grid(
                    Grid(
                        DivLAligned(
                            UkIcon(icon="play"),
                            P(config_name),
                            P(
                                f"{num_items} item{'s' if num_items != 1 else ''}",
                                cls="ml-2 text-xs text-muted-foreground",
                            ),
                            add_button,
                        ),
                        DivRAligned(P(read_only)),
                    ),
                    JsonView(json.loads(value)),
                ),
                cls="list-none",
            ),
            Grid(
                Div(
                    DivHStacked(*[Kbd(s) for s in scope]) if scope else None,
                    Markdown(description),
                ),
                P(config_type),
            ),
            # Default value row
            Grid(
                DivRAligned(P("default")),
                P(default),
            )
            if read_only == "rw"
            else None,
            # Raw JSON fallback update form
            Grid(
                DivRAligned(P("update")),
                Grid(
                    Form(
                        Input(value="update", type="hidden", id="action"),
                        Input(value=config_name, type="hidden", id="key"),
                        Input(value=items_update_value, type="text", id="value"),
                        hx_put=request_url_for("/eosdash/configuration"),
                        hx_target="#page-content",
                        hx_swap="innerHTML",
                    ),
                ),
            )
            if read_only == "rw"
            else None,
            # Last update error
            Grid(
                DivRAligned(P("update error")),
                UpdateError(items_update_error),
            )
            if items_update_error
            else None,
            # Per-item inner cards
            *rows,
            cls="space-y-4 gap-4",
            open=items_update_open,
        ),
        cls="w-full",
    )


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------


def ConfigItemsCard(
    config: dict,
    hint: UiHint,
    config_details: dict[str, dict],
    config_update_latest: dict[str, dict],
    create_config_details: Callable,
) -> Card:
    """Creates a styled configuration card for a list of Pydantic sub-model items.

    Renders a collapsible outer card representing the list field as a whole,
    containing one collapsible inner card per item in the list.  Each inner
    card expands into individual ``ConfigCard`` rows for every field of the
    item's Pydantic sub-model.

    The list length is driven entirely by user interaction — there is no fixed
    maximum.

    An "Add item" control in the outer card header creates a new item
    pre-filled with the sub-model's Pydantic field defaults.  When every
    field has a default, this is a one-click button that appends and PUTs
    immediately.  When the sub-model has fields with no default (e.g.
    ``consumption_wh``/``duration_h`` on ``HomeApplianceCommonSettings``),
    the control instead expands into a small inline form that collects
    those required values first — the PUT is only built, via HTML
    ``required`` inputs, once every missing field is filled in, so an
    invalid item is never persisted.  Each inner card header carries a
    trash icon that arms on first click (showing a red "Confirm delete"
    button via a ``<details>`` toggle) and deletes on the second click,
    with no modal required.
    Per-item field forms are resolved via ``hint_for_indexed_field`` using the
    parent hint's ``item_path``, so per-field UI customisation (dropdowns,
    selects, etc.) is driven entirely by ``UI_HINTS`` entries — no hard-coded
    field-name checks are needed here.

    The outer card always includes a plain-text fallback update form for the
    whole list value so that recovery from a validation error is always
    possible.

    Args:
        config: A single entry from the ``config_details`` dict produced by
            ``create_config_details()``.  Must contain the keys ``"name"``,
            ``"type"``, ``"read-only"``, ``"value"``, ``"default"``,
            ``"description"``, ``"deprecated"``, and ``"scope"``.
        hint: The ``UiHint`` for this field.  Must have ``form == "items"``
            and valid ``item_model`` (resolved via ``resolve_item_model``) and
            ``item_path`` values.  ``max_items_from`` is ignored — the list
            grows and shrinks freely via Add / Delete.
        config_details: The full config detail dict for the current page
            render, used to look up per-item field update state.
        config_update_latest: The module-level dict that tracks the most
            recent update attempt for each config key, with sub-keys
            ``"error"``, ``"value"``, and ``"open"``.
        create_config_details: The ``create_config_details`` callable from
            ``configuration.py``, injected to avoid a circular import.
            Signature: ``(model, values, values_prefix) -> dict[str, dict]``.

    Returns:
        Card: A fully rendered outer ``Card`` component containing the list
        summary with item count and an "Add item" control (a one-click
        button, or an inline required-fields form — see above), description,
        default value row, a raw-JSON fallback update form, an optional
        error row, and one collapsible inner ``Card`` per existing item each
        with a two-click delete control.

    Raises:
        TypeError: If ``update_error``, ``update_value``, or ``update_open``
            retrieved from ``config_update_latest`` are not of the expected
            types (``str | None``, ``str | None``, ``bool | None``
            respectively).  This should never trigger in normal operation but
            is checked explicitly to satisfy static analysis.

    Example:
        Typical call from inside the ``Configuration()`` render loop::

            from akkudoktoreos.server.dash.itemscard import ConfigItemsCard

            hint = UI_HINTS.get(config["name"])
            if hint and hint.form == "items" and not config["deprecated"]:
                rows.append(
                    ConfigItemsCard(
                        config=config,
                        hint=hint,
                        config_details=config_details,
                        config_update_latest=config_update_latest,
                        create_config_details=create_config_details,
                    )
                )
    """
    config_name = config["name"]
    config_type = config["type"]
    read_only = config["read-only"]
    value = config["value"]
    default = config["default"]
    description = config["description"]

    item_model = resolve_item_model(hint)
    item_path = hint.item_path  # e.g. "pvforecast.planes"
    if item_path is None:
        raise ValueError(f"Hint needs item_path to be listed. Got {hint}")
    path_parts = item_path.split(".")  # e.g. ["pvforecast", "planes"]

    items_list = json.loads(value) or []
    num_items = len(items_list)

    # Synthetic wrapper dict so create_config_details can traverse the value:
    #   e.g. {"pvforecast": {"planes": [...]}}
    wrapped = json.loads(value)
    for key in reversed(path_parts):
        wrapped = {key: wrapped}

    # Outer card update state — resolved once before the inner loop
    items_update_error = config_update_latest.get(config_name, {}).get("error")
    items_update_value = config_update_latest.get(config_name, {}).get("value") or value
    items_update_open = config_update_latest.get(config_name, {}).get("open") or False

    # Add button.
    # One-click append when the item model is fully defaulted, otherwise an inline form that
    # collects required fields before the PUT fires (see _add_control / _item_model_defaults
    # docstrings).
    add_button = _add_control(config_name, item_model, items_list, read_only)

    # Build inner cards
    rows = []
    for i in range(num_items):
        item_config = create_config_details(
            item_model,
            wrapped,
            values_prefix=path_parts + [str(i)],
        )
        item_rows = []
        item_update_open = False
        item_value = json.dumps(items_list[i]) if items_list[i] is not None else json.dumps(None)
        is_empty = not items_list[i]

        for field_key in sorted(item_config.keys()):
            sub = item_config[field_key]
            update_error = config_update_latest.get(sub["name"], {}).get("error")
            update_value = config_update_latest.get(sub["name"], {}).get("value")
            update_open = config_update_latest.get(sub["name"], {}).get("open")
            if update_open:
                items_update_open = True  # bubble up to outer card
                item_update_open = True
            # Make mypy happy — should never trigger
            if (
                not isinstance(update_error, (str, type(None)))
                or not isinstance(update_value, (str, type(None)))
                or not isinstance(update_open, (bool, type(None)))
            ):
                error_msg = "update_error or update_value or update_open of wrong type."
                logger.error(error_msg)
                raise TypeError(error_msg)
            sub_hint = hint_for_indexed_field(sub["name"], item_path)
            update_form_factory = (
                resolve_form_factory(sub_hint, config_details) if sub_hint else None
            )
            item_rows.append(
                ConfigCard(
                    sub["name"],
                    sub["type"],
                    sub["read-only"],
                    sub["value"],
                    sub["default"],
                    sub["description"],
                    sub["deprecated"],
                    sub["scope"],
                    update_error,
                    update_value,
                    update_open,
                    update_form_factory,
                )
            )

        rows.append(
            _inner_card(
                config_name=config_name,
                item_path=item_path,
                path_parts=path_parts,
                index=i,
                item_value=item_value,
                is_empty=is_empty,
                read_only=read_only,
                item_rows=item_rows,
                item_update_open=item_update_open,
                delete_control=_delete_control(config_name, items_list, i)
                if read_only == "rw"
                else None,
            )
        )

    return _outer_card(
        config_name=config_name,
        config_type=config_type,
        read_only=read_only,
        value=value,
        default=default,
        description=description,
        scope=config.get("scope"),
        num_items=num_items,
        add_button=add_button,
        items_update_value=items_update_value,
        items_update_error=items_update_error,
        items_update_open=items_update_open,
        rows=rows,
    )
