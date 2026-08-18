"""Generic expandable map-of-sub-model configuration card for EOSdash.

This module provides `ConfigMapCard`, a reusable FastHTML/MonsterUI
card component that renders any ``dict[str, PydanticSubModel]`` config field
as a collapsible outer card containing one collapsible inner card per map
entry, keyed by a user-supplied string name.

It is intentionally free of imports from ``configuration.py`` to avoid
circular dependencies.  The one runtime dependency on
``create_config_details`` is injected by the caller.

The structure mirrors ``itemscard.py`` with these key differences:

- The stored value is ``dict[str, dict]`` rather than ``list[dict]``.
- The "Add entry" control includes a text input for the key name.
- Delete removes by key rather than by index.
- Inner card headers display the string key instead of a numeric index.
- ``create_config_details`` is called with the string key as the final
  ``values_prefix`` segment.

Typical usage in ``configuration.py``::

    from akkudoktoreos.server.dash.mapcard import ConfigMapCard

    hint = UI_HINTS.get(config["name"])
    if hint and hint.form == "map_items" and not config["deprecated"]:
        rows.append(
            ConfigMapCard(
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
    items_map: dict,
    item_model: Any,
    config_id: str,
    read_only: str,
) -> Any:
    """Build the "Add entry" control.

    Always includes a text input for the new key name. When the item model
    can be fully defaulted, that's the only input needed — clicking "Add
    entry" reads the key and submits the map with the new key set to the
    model defaults. When required fields have no default (e.g.
    `consumption_wh`/`duration_h` on `HomeApplianceCommonSettings`), the
    control also renders inputs for those fields, all wrapped in a form so
    HTML `required` blocks submission until the key and every required
    field are filled in — otherwise clicking "Add entry" would persist an
    entry that fails the model's own validation.

    If the typed key already exists the existing entry is overwritten — this
    is intentional and allows renaming-by-copy when combined with delete.

    Args:
        config_name: Dotted config key name.
        items_map: The current map, used as the base for the JS merge.
        item_model: The Pydantic model class or instance for one map entry.
        config_id: CSS-safe version of ``config_name`` (dots replaced with
            hyphens), used to scope element ids and the key input's name.

    Returns:
        A ``Form`` containing the key input, any required-field inputs, and
        the "Add entry" button.
    """
    new_entry_defaults, required_missing = item_model_defaults(item_model)

    id_prefix = f"{config_id}-new-entry"
    inputs, js_pairs = required_field_inputs(item_model, required_missing, id_prefix)
    extra_js = f"{{ {', '.join(js_pairs)} }}" if js_pairs else "{}"

    current_json = json.dumps(items_map)
    defaults_json = json.dumps(new_entry_defaults)

    build_value_expr = f"""(() => {{
        const k = document.querySelector("[name='{config_id}_new_key']").value.trim();
        if (!k) return {json.dumps(json.dumps(items_map))};
        const defaults = {defaults_json};
        const extra = {extra_js};
        Object.assign(defaults, extra);
        if ('device_id' in defaults) defaults.device_id = k;
        const updated = Object.assign({{}}, {current_json}, {{ [k]: defaults }});
        return JSON.stringify(updated);
    }})()"""

    return Form(
        Grid(
            Input(
                placeholder="Entry name / key",
                name=f"{config_id}_new_key",
                id=f"{config_id}-new-key",
                required=True,
                cls="border rounded px-3 py-2 text-sm",
            ),
            *inputs,
            cols=2,
            cls="gap-2",
        ),
        ConfigButton(
            UkIcon("plus"),
            " Add entry",
            hx_put=request_url_for("/eosdash/configuration"),
            hx_target="#page-content",
            hx_swap="innerHTML",
            hx_vals=f'js:{{ action: "update", key: "{config_name}", value: {build_value_expr} }}',
            cls="mt-2",
        ),
        cls="space-y-2 mt-3",
    )


def _delete_control(config_name: str, items_map: dict, key: str) -> Details:
    """Build the two-click delete control for a single inner entry card header.

    The first click opens a ``<details>`` panel revealing a red "Confirm
    delete" button.  Clicking outside collapses it.  The second click submits
    an ``hx_put`` with the map minus the given key.

    Args:
        config_name: Dotted config key name, e.g. ``"devices.batteries"``.
        items_map: The current full map of entries.
        key: The string key of the entry to delete.

    Returns:
        A ``Details`` component implementing the two-click confirm pattern.
    """
    remaining = {k: v for k, v in items_map.items() if k != key}
    remaining_json = json.dumps(json.dumps(remaining))
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
    key: str,
    item_value: str,
    is_empty: bool,
    read_only: str,
    item_rows: list,
    item_update_open: bool,
    delete_control: Optional[Details],
) -> Card:
    """Render a single collapsible inner card for one map entry.

    Args:
        config_name: Dotted config key of the parent map field.
        item_path: Dotted path prefix for this entry type, e.g.
            ``"devices.batteries"``.
        key: The string key identifying this entry in the map.
        item_value: JSON-encoded current value of this entry.
        is_empty: ``True`` when the entry dict is falsy (empty or ``None``).
        read_only: ``"rw"`` or ``"ro"`` inherited from the parent field.
        item_rows: Pre-built list of ``ConfigCard`` children for this entry.
        item_update_open: Whether this card should start expanded.
        delete_control: The two-click delete ``Details`` widget, or ``None``
            for read-only fields.

    Returns:
        A ``Card`` component for this map entry.
    """
    return Card(
        Details(
            Summary(
                Grid(
                    Grid(
                        DivLAligned(
                            UkIcon(icon="play"),
                            H4(
                                f"{item_path}.{key}",
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
    num_entries: int,
    items_update_value: str,
    items_update_error: Optional[str],
    items_update_open: bool,
    rows: list,
    add_control: Optional[Grid],
) -> Card:
    """Render the outer collapsible card for the whole map field.

    Args:
        config_name: Dotted config key name.
        config_type: Human-readable type string from config details.
        read_only: ``"rw"`` or ``"ro"``.
        value: JSON-encoded current map value.
        default: JSON-encoded default value.
        description: Field description text.
        num_entries: Current number of entries, shown as a badge.
        items_update_value: Value to pre-fill the fallback text input.
        items_update_error: Error string from the last failed update, or
            ``None``.
        items_update_open: Whether the outer card starts expanded.
        rows: Pre-built list of inner ``Card`` components.
        add_control: The "Add entry" ``Grid`` widget, or ``None`` for
            read-only fields.

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
                                f"{num_entries} entr{'ies' if num_entries != 1 else 'y'}",
                                cls="ml-2 text-xs text-muted-foreground",
                            ),
                        ),
                        DivRAligned(P(read_only)),
                    ),
                    JsonView(json.loads(value)),
                ),
                cls="list-none",
            ),
            # Add entry control below summary
            add_control,
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
            # Per-entry inner cards
            *rows,
            cls="space-y-4 gap-4",
            open=items_update_open,
        ),
        cls="w-full",
    )


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------


def ConfigMapCard(
    config: dict,
    hint: UiHint,
    config_details: dict[str, dict],
    config_update_latest: dict[str, dict],
    create_config_details: Callable,
) -> Card:
    """Creates a styled configuration card for a map of Pydantic sub-model entries.

    Renders a collapsible outer card representing the map field as a whole,
    containing one collapsible inner card per map entry keyed by a string
    name.  Each inner card expands into individual ``ConfigCard`` rows for
    every field of the entry's Pydantic sub-model.

    The map contents are driven entirely by user interaction.  An "Add entry"
    control at the bottom of the outer card accepts a key name and appends a
    new entry pre-filled with the sub-model's Pydantic field defaults.  Each
    inner card header carries a trash icon that arms on first click (showing
    a red "Confirm delete" button via a ``<details>`` toggle) and deletes on
    the second click, with no modal required.

    Per-entry field forms are resolved via ``hint_for_indexed_field`` using
    the parent hint's ``item_path``, so per-field UI customisation
    (dropdowns, selects, etc.) is driven entirely by ``UI_HINTS`` entries —
    no hard-coded field-name checks are needed here.

    The outer card always includes a plain-text fallback update form for the
    whole map value so that recovery from a validation error is always
    possible.

    Args:
        config: A single entry from the ``config_details`` dict produced by
            ``create_config_details()``.  Must contain the keys ``"name"``,
            ``"type"``, ``"read-only"``, ``"value"``, ``"default"``,
            ``"description"``, ``"deprecated"``, and ``"scope"``.
        hint: The ``UiHint`` for this field.  Must have
            ``form == "map_items"`` and valid ``item_model`` (resolved via
            ``resolve_item_model``) and ``item_path`` values.
        config_details: The full config detail dict for the current page
            render, used to look up per-entry field update state.
        config_update_latest: The module-level dict that tracks the most
            recent update attempt for each config key, with sub-keys
            ``"error"``, ``"value"``, and ``"open"``.
        create_config_details: The ``create_config_details`` callable from
            ``configuration.py``, injected to avoid a circular import.
            Signature: ``(model, values, values_prefix) -> dict[str, dict]``.

    Returns:
        Card: A fully rendered outer ``Card`` component containing the map
        summary with entry count, description, default value row, a raw-JSON
        fallback update form, an optional error row, one collapsible inner
        ``Card`` per existing entry each with a two-click delete control, and
        an "Add entry" control at the bottom.

    Raises:
        TypeError: If ``update_error``, ``update_value``, or ``update_open``
            retrieved from ``config_update_latest`` are not of the expected
            types (``str | None``, ``str | None``, ``bool | None``
            respectively).  This should never trigger in normal operation but
            is checked explicitly to satisfy static analysis.

    Example:
        Typical call from inside the ``Configuration()`` render loop::

            from akkudoktoreos.server.dash.mapcard import ConfigMapCard

            hint = UI_HINTS.get(config["name"])
            if hint and hint.form == "map_items" and not config["deprecated"]:
                rows.append(
                    ConfigMapCard(
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
    config_id = config_name.lower().replace(".", "-")

    item_model = resolve_item_model(hint)
    item_path = hint.item_path  # e.g. "devices.batteries"
    if item_path is None:
        raise ValueError(f"Hint needs item_path to be mapped. Got {hint}")
    path_parts = item_path.split(".")  # e.g. ["devices", "batteries"]

    items_map = json.loads(value) or {}
    num_entries = len(items_map)

    # Outer card update state — resolved once before the inner loop
    items_update_error = config_update_latest.get(config_name, {}).get("error")
    items_update_value = config_update_latest.get(config_name, {}).get("value") or value
    items_update_open = config_update_latest.get(config_name, {}).get("open") or False

    # Add entry control (key input + button) shown at the bottom of the card.
    # One-click append when the item model is fully defaulted, otherwise an inline form that
    # collects required fields before the PUT fires (see _add_control / _item_model_defaults
    # docstrings).
    add_control = _add_control(
        config_name=config_name,
        items_map=items_map,
        item_model=item_model,
        config_id=config_id,
        read_only=read_only,
    )

    # Build inner cards — one per map key, sorted for stable ordering
    rows = []
    for key in sorted(items_map.keys()):
        entry = items_map[key]

        # Synthetic wrapper: e.g. {"devices": {"batteries": {"bat1": {...}}}}
        wrapped = {key: entry}
        for part in reversed(path_parts):
            wrapped = {part: wrapped}

        item_config = create_config_details(
            item_model,
            wrapped,
            values_prefix=path_parts + [key],
        )

        item_rows = []
        item_update_open = False
        item_value = json.dumps(entry) if entry is not None else json.dumps(None)
        is_empty = not entry

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
                key=key,
                item_value=item_value,
                is_empty=is_empty,
                read_only=read_only,
                item_rows=item_rows,
                item_update_open=item_update_open,
                delete_control=_delete_control(config_name, items_map, key)
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
        num_entries=num_entries,
        items_update_value=items_update_value,
        items_update_error=items_update_error,
        items_update_open=items_update_open,
        rows=rows,
        add_control=add_control,
    )
