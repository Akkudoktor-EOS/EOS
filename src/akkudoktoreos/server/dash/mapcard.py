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
- With ``UiHint.item_discriminator`` the entries may have different models
  (a discriminated union, e.g. the device controllers selected by
  ``controller_type``): each entry renders the fields of its own model, the
  discriminator field is read-only, and "Add entry" asks for the type first.

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

from fasthtml.common import Fieldset, Option, Select
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
    VALIDATE_FORM_ATTRS,
    CollapsedSection,
    ConfigButton,
    ConfigCard,
    JsonView,
    UpdateError,
    make_config_update_optional_model_form,
)
from akkudoktoreos.server.dash.context import request_url_for
from akkudoktoreos.server.dash.markdown import Markdown
from akkudoktoreos.server.dash.uihints import (
    UiHint,
    discriminated_item_models,
    hint_for_indexed_field,
    optional_model_rows,
    resolve_entry_model,
    resolve_form_factory,
    resolve_item_model,
)

# ---------------------------------------------------------------------------
# Internal helpers
# ---------------------------------------------------------------------------


def _add_control(
    config_name: str,
    items_map: dict,
    entry_models: dict[str, Any],
    discriminator: Optional[str],
    config_id: str,
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

    With a discriminator the control also renders a select of the entry
    types. Each type gets its own group of required-field inputs; only the
    group of the selected type is enabled and visible, so `required` checks
    just the fields of that type. The new entry gets the defaults of the
    selected type's model plus the discriminator value.

    If the typed key already exists the existing entry is overwritten — this
    is intentional and allows renaming-by-copy when combined with delete.

    Laid out like the update forms of the other settings: the label "add entry" on the left,
    the inputs one below the other and the button on the right.

    Args:
        config_name: Dotted config key name.
        items_map: The current map, used as the base for the JS merge.
        entry_models: The Pydantic model class (or instance) of a new entry
            per entry type, e.g. ``{"sma:energy-meter": SmaEnergyMeterSettings}``.
            Holds exactly one model under the key ``""`` when all entries share
            one model.
        discriminator: Name of the field that selects the entry type, or
            ``None`` when all entries share one model.
        config_id: CSS-safe version of ``config_name`` (dots replaced with
            hyphens), used to scope element ids and the input names.

    Returns:
        A ``Grid`` with the label and a ``Form`` containing the key input, the type select (with a
        discriminator), the required-field inputs, and the "Add entry" button.
    """
    defaults_by_type: dict[str, dict] = {}
    extra_by_type: list[str] = []
    groups: list[Fieldset] = []
    for index, (entry_type, entry_model) in enumerate(entry_models.items()):
        defaults, required_missing = item_model_defaults(entry_model)
        # The type select sets the discriminator (it has no default, see ControllerBaseSettings).
        required_missing = [name for name in required_missing if name != discriminator]
        id_prefix = f"{config_id}-new-entry" + (f"-{index}" if discriminator else "")
        inputs, js_pairs = required_field_inputs(entry_model, required_missing, id_prefix)
        defaults_by_type[entry_type] = defaults
        extra_by_type.append(f"{json.dumps(entry_type)}: () => ({{ {', '.join(js_pairs)} }})")
        selected = index == 0
        groups.append(
            Fieldset(
                Grid(*inputs, cols=1, cls="gap-2"),
                data_entry_type=entry_type,
                disabled=not selected,
                hidden=not selected,
            )
        )

    type_select = None
    type_expr = '""'
    if discriminator:
        type_name = f"{config_id}_new_type"
        type_select = Select(
            *[
                Option(entry_type, value=entry_type, selected=index == 0)
                for index, entry_type in enumerate(entry_models)
            ],
            id=f"{config_id}-new-type",
            name=type_name,
            onchange=(
                "this.form.querySelectorAll('fieldset[data-entry-type]').forEach(f => {"
                " const on = f.dataset.entryType === this.value;"
                " f.disabled = !on; f.hidden = !on; })"
            ),
            cls="border rounded px-3 py-2 text-sm",
        )
        type_expr = f"document.querySelector(\"[name='{type_name}']\").value"

    current_json = json.dumps(items_map)
    discriminator_js = f"defaults[{json.dumps(discriminator)}] = t;" if discriminator else ""

    build_value_expr = f"""(() => {{
        const k = document.querySelector("[name='{config_id}_new_key']").value.trim();
        if (!k) return {json.dumps(json.dumps(items_map))};
        const t = {type_expr};
        const defaults = Object.assign({{}}, {json.dumps(defaults_by_type)}[t]);
        Object.assign(defaults, ({{ {", ".join(extra_by_type)} }})[t]());
        {discriminator_js}
        if ('device_id' in defaults) defaults.device_id = k;
        const updated = Object.assign({{}}, {current_json}, {{ [k]: defaults }});
        return JSON.stringify(updated);
    }})()"""

    form = Form(
        Grid(
            Div(
                P("name (required)", cls="text-xs text-muted-foreground"),
                Input(
                    placeholder="Entry name / key",
                    name=f"{config_id}_new_key",
                    id=f"{config_id}-new-key",
                    required=True,
                    cls="border rounded px-3 py-2 text-sm",
                ),
            ),
            Div(
                P(discriminator, cls="text-xs text-muted-foreground"),
                type_select,
            )
            if discriminator
            else None,
            cols=1,
            cls="gap-2",
        ),
        *groups,
        ConfigButton(
            UkIcon("plus"),
            " Add entry",
            hx_put=request_url_for("/eosdash/configuration"),
            hx_target="#page-content",
            hx_swap="innerHTML",
            hx_vals=f'js:{{ action: "update", key: "{config_name}", value: {build_value_expr} }}',
            cls=" mt-2",
            **VALIDATE_FORM_ATTRS,
        ),
        cls="space-y-2",
    )
    return Grid(
        DivRAligned(P("add entry")),
        form,
        id=f"{config_id}-add-entry",
        cls="mt-3",
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
    entry_type: Optional[str] = None,
) -> Card:
    """Render a single collapsible inner card for one map entry.

    The header shows the name of the entry (and its type), the JSON value of the entry is in a
    collapsed section below the field rows.

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
        entry_type: The discriminator value of the entry, e.g. the controller type, or
            ``None`` when all entries share one model.

    Returns:
        A ``Card`` component for this map entry.
    """
    return Card(
        Details(
            Summary(
                Grid(
                    DivLAligned(
                        UkIcon(icon="play"),
                        H4(
                            f"{item_path}.{key}",
                            cls="text-muted-foreground" if is_empty else "",
                        ),
                        P(entry_type, cls="text-sm text-muted-foreground") if entry_type else None,
                        delete_control,
                    ),
                    DivRAligned(
                        P(
                            "empty" if is_empty else read_only,
                            cls="text-xs text-muted-foreground" if is_empty else "",
                        ),
                    ),
                ),
                cls="list-none",
            ),
            *item_rows,
            CollapsedSection(
                "JSON",
                JsonView(json.loads(item_value)),
                id=f"{config_name.lower().replace('.', '-')}-{key}-json",
            ),
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
    advanced_open: bool,
    rows: list,
    add_control: Optional[Grid],
) -> Card:
    """Render the outer collapsible card for the whole map field.

    The JSON value, the default and the raw JSON update input are in a collapsed
    "Advanced (JSON)" section, which opens when the last update of the map failed. The error
    of that update is shown above it.

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
        advanced_open: Whether the "Advanced (JSON)" section starts expanded, i.e. the
            latest update was one of the whole field and it failed.
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
            # Last update error
            Grid(
                DivRAligned(P("update error")),
                UpdateError(items_update_error),
            )
            if items_update_error
            else None,
            CollapsedSection(
                "Advanced (JSON)",
                JsonView(json.loads(value)),
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
                open=advanced_open,
                id=f"{config_name.lower().replace('.', '-')}-advanced",
            ),
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
    open_name: Optional[str] = None,
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
        open_name: Dotted name of this map or one of its entries to show expanded, e.g.
            ``"adapter.control.controllers.inverter1"``. An entry opens the outer card, too.

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
    if item_model is None:
        raise ValueError(f"Hint for {config_name} needs item_model to be listed. Got {hint}")
    item_path = hint.item_path  # e.g. "devices.batteries"
    if item_path is None:
        raise ValueError(f"Hint for {config_name} needs item_path to be listed. Got {hint}")
    path_parts = item_path.split(".")  # e.g. ["devices", "batteries"]

    items_map = json.loads(value) or {}
    num_entries = len(items_map)

    # Outer card update state — resolved once before the inner loop
    items_update_error = config_update_latest.get(config_name, {}).get("error")
    items_update_value = config_update_latest.get(config_name, {}).get("value") or value
    items_update_open = config_update_latest.get(config_name, {}).get("open") or False
    # The error stays until the field is updated again; open only right after the failure.
    advanced_open = bool(items_update_error) and items_update_open
    if open_name and (open_name == config_name or open_name.startswith(f"{config_name}.")):
        items_update_open = True

    # Add entry control (key input + button) shown at the bottom of the card.
    # One-click append when the item model is fully defaulted, otherwise an inline form that
    # collects required fields before the PUT fires (see _add_control / _item_model_defaults
    # docstrings).
    discriminator = hint.item_discriminator
    entry_models: dict[str, Any] = (
        dict(discriminated_item_models(hint)) if discriminator else {"": item_model}
    )
    add_control = _add_control(
        config_name=config_name,
        items_map=items_map,
        entry_models=entry_models,
        discriminator=discriminator,
        config_id=config_id,
    )

    # Build inner cards — one per map key, sorted for stable ordering
    rows = []
    for key in sorted(items_map.keys()):
        entry = items_map[key]

        # Synthetic wrapper: e.g. {"devices": {"batteries": {"bat1": {...}}}}
        wrapped = {key: entry}
        for part in reversed(path_parts):
            wrapped = {part: wrapped}

        item_rows: list = []
        entry_model = resolve_entry_model(hint, entry)
        if entry_model is None:
            # The type of the entry selects no model: show the entry as JSON only, it can be
            # fixed with the raw JSON update form of the outer card.
            item_config: dict[str, dict] = {}
            entry_type = entry.get(discriminator) if isinstance(entry, dict) else None
            item_rows.append(
                P(
                    f"Unknown {discriminator} {json.dumps(entry_type)},"
                    f" expected one of {', '.join(entry_models)}.",
                    cls="text-sm text-destructive",
                )
            )
        else:
            item_config = create_config_details(
                entry_model,
                wrapped,
                values_prefix=path_parts + [key],
            )
        entry_name = f"{item_path}.{key}"
        switched_names, hidden_names = optional_model_rows(
            item_config, lambda name: hint_for_indexed_field(name, item_path)
        )
        # Options of a field may come from the page or from the same entry
        entry_config_details = {**config_details, **item_config}
        item_update_open = open_name == entry_name
        item_value = json.dumps(entry) if entry is not None else json.dumps(None)
        is_empty = not entry

        for field_key in sorted(item_config.keys()):
            if field_key in hidden_names:
                continue
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
            update_form_factory: Optional[Callable]
            if field_key in switched_names:
                update_form_factory = make_config_update_optional_model_form(
                    sub["optional-model"], sub["value"]
                )
            elif sub_hint:
                update_form_factory = resolve_form_factory(
                    sub_hint, entry_config_details, entry_name=entry_name
                )
            else:
                update_form_factory = None
            # The type of an entry is not changed in place: the required fields differ by type.
            sub_read_only = (
                "ro"
                if discriminator and sub["name"] == f"{item_path}.{key}.{discriminator}"
                else sub["read-only"]
            )
            item_rows.append(
                ConfigCard(
                    sub["name"],
                    sub["type"],
                    sub_read_only,
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
                entry_type=entry.get(discriminator)
                if discriminator and isinstance(entry, dict)
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
        advanced_open=advanced_open,
        rows=rows,
        add_control=add_control,
    )
