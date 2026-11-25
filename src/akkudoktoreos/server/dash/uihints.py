"""UI hint registry for EOSdash configuration forms.

This module decouples UI rendering decisions from both the domain models and the
main ``Configuration()`` render function.  Instead of a long if/elif chain that
maps config field paths to form factories, all those decisions live here as
structured ``UiHint`` entries in ``UI_HINTS``.

Typical usage in ``configuration.py``::

    from akkudoktoreos.server.dash.uihints import UI_HINTS, resolve_form_factory

    hint = UI_HINTS.get(config["name"])
    if hint and hint.form == "items":
        rows.append(ConfigItemsCard(config, hint, config_details, config_update_latest))
    elif not config["deprecated"]:
        update_form_factory = resolve_form_factory(hint, config_details) if hint else None
        rows.append(ConfigCard(..., update_form_factory))

``ConfigItemsCard`` must live in ``configuration.py`` because it depends on
``create_config_details`` and ``config_update_latest``.  This module only
carries the *data* that drives it.
"""

import json
from dataclasses import dataclass, field
from typing import (
    Annotated,
    Any,
    Callable,
    Literal,
    Optional,
    Union,
    get_args,
    get_origin,
)

from pydantic import BaseModel

from akkudoktoreos.server.dash.carditems import optional_model_is_on
from akkudoktoreos.server.dash.components import (
    make_config_update_lazy_select_form,
    make_config_update_list_form,
    make_config_update_map_form,
    make_config_update_time_windows_windows_form,
    make_config_update_value_form,
)

# ---------------------------------------------------------------------------
# Form type literals
# ---------------------------------------------------------------------------

UiFormType = Literal[
    "text",  # plain text input (default)
    "select",  # single-value dropdown
    "select_list",  # add/delete multi-value list
    "select_lazy",  # server-filtered select for large option sets
    "map",  # key/value pair editor
    "time_windows",  # time-window sequence editor
    "items",  # expandable list of sub-model cards
    "map_items",  # expandable map of sub-model cards
]


# ---------------------------------------------------------------------------
# UiHint dataclass
# ---------------------------------------------------------------------------


@dataclass
class UiHint:
    """Rendering hints for a single configuration field.

    Attributes:
        form:
            Which form widget to use.  Defaults to ``"text"``.

        options:
            Static allowed values for ``"select"`` / ``"select_list"``.

        options_from:
            Dotted config-field path whose runtime value provides the
            option list (JSON-encoded ``list[str]``, or a JSON object whose
            keys are the options, e.g. ``"devices.batteries"``).  A list of
            paths gives the sorted union of their options.  Takes
            precedence over ``options`` when both are set.  A ``"select"``
            without any option falls back to the plain text input.

        free_text:
            *``"select_list"`` only.*  Values are typed into a text input
            that suggests the options, so a value that is no option can be
            added, too (e.g. a new measurement key).

        options_from_entry:
            *Fields of ``"items"`` / ``"map_items"`` entries only.*  Name of
            a field of the same entry whose value provides the option list,
            e.g. ``"shared_data_keys"`` of a device controller.  Used like
            ``options_from`` (for ``"map"``: the values) and takes precedence
            over it.

        options_source:
            ``select_lazy`` only. Key into LAZY_OPTIONS_SOURCES identifying the
            callable that lazily returns the full (potentially huge) candidate
            list, e.g. "pvforecast.cec_modules". Distinct from options_from, which
            reads a small already-resolved list out of config_details.

        param_from:
            Dotted config-field path for a secondary runtime parameter.
            Used by ``"map"`` for the *keys* dropdown.

        append_none:
            Append ``"None"`` to the resolved option list.  Useful for
            nullable single-value selects such as ``*.provider`` fields.

        value_description:
            Label for the extra numeric column in the ``"time_windows"``
            form (e.g. ``"electricity_price_kwh [Amt/kWh]"``).  When
            ``None`` no value column is rendered.

        item_model:
            *``"items"`` only.*  The Pydantic model class (or instance)
            whose fields define the per-item sub-cards, e.g.
            ``PVForecastPlaneSetting``.  Set via ``_ensure_item_models()``
            at first use to avoid circular imports.  With
            ``item_discriminator`` it is a discriminated union of models
            instead (``Annotated[Union[...], Field(discriminator=...)]``).

        item_discriminator:
            *``"map_items"`` only.*  Name of the field whose ``Literal``
            value selects the model of an entry from the union in
            ``item_model``, e.g. ``"controller_type"``.  ``None`` when all
            entries share one model.

        item_path:
            *``"items"`` only.*  Dotted path that locates the list inside
            the synthetic config dict built from the field value.  Used to
            construct the ``values_prefix`` for ``create_config_details``.

            Example: planes are wrapped as
            ``{"pvforecast": {"planes": <value>}}`` so ``item_path`` is
            ``"pvforecast.planes"``.

        max_items_from:
            *``"items"`` only.*  Dotted config-field path whose integer
            value caps the number of rendered sub-cards (e.g.
            ``"pvforecast.max_planes"``).  When ``None`` the length of
            the actual list is used instead.

        optional_switch:
            *Optional sub-model fields only* (``Optional[<sub-model>]``
            below the top level).  ``True`` (default): the field gets an
            on/off switch, its sub-fields are hidden while it is ``null``.
            ``False``: no switch and no row of its own, the sub-fields are
            always shown (e.g. for a stored result rather than a setting).
    """

    form: UiFormType = "text"

    # select / select_list / map
    options: list[str] = field(default_factory=list)
    options_from: Optional[Union[str, list[str]]] = None
    free_text: bool = False
    options_from_entry: Optional[str] = None
    options_source: Optional[str] = None
    param_from: Optional[str] = None
    append_none: bool = False

    # time_windows
    value_description: Optional[str] = None

    # items
    item_model: Optional[Any] = None
    item_path: Optional[str] = None
    max_items_from: Optional[str] = None
    item_discriminator: Optional[str] = None

    # optional sub-model
    optional_switch: bool = True


# ---------------------------------------------------------------------------
# Lazy Registry
# ---------------------------------------------------------------------------

LazyOptionsSource = Callable[[], list[str]]

LAZY_OPTIONS_SOURCES: dict[str, LazyOptionsSource] = {}


def register_lazy_options_source(key: str, source: LazyOptionsSource) -> None:
    """Register a lazy options callable that provides the options list.

    The callable shall provide the full candidate list for a "select_lazy" hint's
    options_source.

    The callable should be cheap to call repeatedly (e.g. wrapped in
    caching) since it is invoked once per keystroke-driven search request —
    filtering happens in this process, not inside the callable itself.
    """
    LAZY_OPTIONS_SOURCES[key] = source


# ---------------------------------------------------------------------------
# Registry
# ---------------------------------------------------------------------------

UI_HINTS: dict[str, UiHint] = {
    # ------------------------------------------------------------------
    # Adapter
    # ------------------------------------------------------------------
    "adapter.provider": UiHint(
        form="select_list",
        options_from="adapter.providers",
    ),
    # - control: one card per controller, its fields depend on controller_type
    "adapter.control.controllers": UiHint(
        form="map_items",
        item_path="adapter.control.controllers",
        item_discriminator="controller_type",
    ),
    "adapter.control.controllers.battery_id": UiHint(
        form="select",
        options_from="devices.batteries",
    ),
    "adapter.control.controllers.grid_power_key": UiHint(
        form="select",
        options_from="adapter.control.shared_data_keys",
    ),
    # Readings of the controllers mapped to measurements: pick the shared data keys
    **{
        f"adapter.control.{role}_emr_data_keys": UiHint(
            form="select_list",
            options_from="adapter.control.shared_data_keys",
        )
        for role in ("load", "grid_export", "grid_import", "pv_production")
    },
    "adapter.control.device_measurement_data_keys": UiHint(
        form="map",
        param_from="devices.measurement_keys",
        options_from="adapter.control.shared_data_keys",
    ),
    "adapter.homeassistant.config_entity_ids": UiHint(
        form="map",
        options_from="adapter.homeassistant.homeassistant_entity_ids",
    ),
    "adapter.homeassistant.load_emr_entity_ids": UiHint(
        form="select_list",
        options_from="adapter.homeassistant.homeassistant_entity_ids",
    ),
    "adapter.homeassistant.grid_export_emr_entity_ids": UiHint(
        form="select_list",
        options_from="adapter.homeassistant.homeassistant_entity_ids",
    ),
    "adapter.homeassistant.grid_import_emr_entity_ids": UiHint(
        form="select_list",
        options_from="adapter.homeassistant.homeassistant_entity_ids",
    ),
    "adapter.homeassistant.pv_production_emr_entity_ids": UiHint(
        form="select_list",
        options_from="adapter.homeassistant.homeassistant_entity_ids",
    ),
    "adapter.homeassistant.device_measurement_entity_ids": UiHint(
        form="map",
        param_from="devices.measurement_keys",
        options_from="adapter.homeassistant.homeassistant_entity_ids",
    ),
    "adapter.homeassistant.device_instruction_entity_ids": UiHint(
        form="select_list",
        options_from="adapter.homeassistant.eos_device_instruction_entity_ids",
    ),
    "adapter.homeassistant.solution_entity_ids": UiHint(
        form="select_list",
        options_from="adapter.homeassistant.eos_solution_entity_ids",
    ),
    # ------------------------------------------------------------------
    # Database
    # ------------------------------------------------------------------
    "database.provider": UiHint(
        form="select",
        options_from="database.providers",
        append_none=True,
    ),
    # ------------------------------------------------------------------
    # Devices
    # ------------------------------------------------------------------
    # - batteries
    "devices.batteries": UiHint(
        form="map_items",
        item_path="devices.batteries",
    ),
    "devices.electric_vehicles": UiHint(
        form="map_items",
        item_path="devices.electric_vehicles",
    ),
    # Stored capacity estimate: a result, not a setting to switch on or off
    "devices.batteries.capacity_estimate": UiHint(optional_switch=False),
    "devices.electric_vehicles.capacity_estimate": UiHint(optional_switch=False),
    # - home_appliances
    "devices.home_appliances": UiHint(
        form="map_items",
        item_path="devices.home_appliances",
    ),
    # Sub-field hint for the time_windows field inside each appliance entry
    "devices.home_appliances.cycle_time_windows.windows": UiHint(
        form="time_windows",
        value_description="cycle index (0-based)",
    ),
    # - inverters
    "devices.inverters": UiHint(
        form="map_items",
        item_path="devices.inverters",
    ),
    # Sub-field hint for the load_power_w_key field inside each inverter entry
    "devices.inverters.load_power_w_key": UiHint(
        form="select", options=["loadforecast_power_w", "null"]
    ),
    # Sub-field hint for the pv_power_w_key field inside each inverter entry
    "devices.inverters.pv_power_w_key": UiHint(
        form="select", options=["pvforecast_dc_power_w", "null"]
    ),
    # ------------------------------------------------------------------
    # Electricity fee
    # ------------------------------------------------------------------
    "elecfee.provider": UiHint(
        form="select",
        options_from="elecfee.providers",
        append_none=True,
    ),
    "elecfee.elecfeefixed.consumption_amt_kwh.windows": UiHint(
        form="time_windows",
        value_description="consumption_amt_kwh [Amt/kWh]",
    ),
    "elecfee.elecfeefixed.consumption_percent_amt.windows": UiHint(
        form="time_windows",
        value_description="consumption_percent_amt [%]",
    ),
    "elecfee.elecfeefixed.feedin_amt_kwh.windows": UiHint(
        form="time_windows",
        value_description="feedin_amt_kwh [Amt/kWh]",
    ),
    "elecfee.elecfeefixed.feedin_percent_amt.windows": UiHint(
        form="time_windows",
        value_description="feedin_percent_amt [%]",
    ),
    # ------------------------------------------------------------------
    # Electricity price
    # ------------------------------------------------------------------
    "elecprice.provider": UiHint(
        form="select",
        options_from="elecprice.providers",
        append_none=True,
    ),
    "elecprice.elecpricefixed.elecprice_marketprice_amt_kwh.windows": UiHint(
        form="time_windows",
        value_description="elecprice_marketprice_amt_kwh [Amt/kWh]",
    ),
    # ------------------------------------------------------------------
    # EMS
    # ------------------------------------------------------------------
    "ems.mode": UiHint(
        form="select",
        options_from="ems.modes",
    ),
    # ------------------------------------------------------------------
    # Feed-in Tariff
    # ------------------------------------------------------------------
    "feedintariff.provider": UiHint(
        form="select",
        options_from="feedintariff.providers",
        append_none=True,
    ),
    "feedintariff.feedintarifffixed.feed_in_tariff_amt_kwh.windows": UiHint(
        form="time_windows",
        value_description="feed_in_tariff_amt_kwh [Amt/kWh]",
    ),
    # ------------------------------------------------------------------
    # Load
    # ------------------------------------------------------------------
    "load.provider": UiHint(
        form="select",
        options_from="load.providers",
        append_none=True,
    ),
    # ------------------------------------------------------------------
    # Measurement
    # ------------------------------------------------------------------
    # The energy meter reading keys define measurement keys: any key can be typed, the defined
    # keys are offered. Readings of device controllers are mapped in adapter.control.
    **{
        f"measurement.{name}": UiHint(
            form="select_list",
            options_from="measurement.keys",
            free_text=True,
        )
        for name in (
            "load_emr_keys",
            "grid_export_emr_keys",
            "grid_import_emr_keys",
            "pv_production_emr_keys",
        )
    },
    # ------------------------------------------------------------------
    # Optimization
    # ------------------------------------------------------------------
    "optimization.algorithm": UiHint(
        form="select",
        options_from="optimization.algorithms",
    ),
    # ------------------------------------------------------------------
    # PV forecast — planes
    # item_model is populated lazily by _ensure_item_models() below.
    # ------------------------------------------------------------------
    "pvforecast.provider": UiHint(
        form="select",
        options_from="pvforecast.providers",
        append_none=True,
    ),
    "pvforecast.planes": UiHint(
        form="items",
        item_path="pvforecast.planes",
        max_items_from="pvforecast.max_planes",
    ),
    # Plain text: the "select" widget has no manual-entry fallback, but
    # adapter.homeassistant.homeassistant_entity_ids only resolves when
    # SUPERVISOR_TOKEN is set, which leaves standalone setups (using this
    # provider's own base_url/token) with an unusable, permanently empty
    # dropdown.
    "pvforecast.homeassistant.entity_id": UiHint(form="text"),
    # Per-plane sub-fields; resolved by hint_for_indexed_field()
    "pvforecast.planes.pvtechchoice": UiHint(
        form="select",
        options=["crystSi", "CIS", "CdTe", "Unknown"],
    ),
    "pvforecast.planes.mountingplace": UiHint(
        form="select",
        options=["free", "building"],
    ),
    "pvforecast.planes.inverter_model": UiHint(
        form="select_lazy",
        options_source="pvlib.inverters",
    ),
    "pvforecast.planes.module_model": UiHint(
        form="select_lazy",
        options_source="pvlib.modules",
    ),
    # ------------------------------------------------------------------
    # Weather
    # ------------------------------------------------------------------
    "weather.providers": UiHint(
        form="select_list",
        options_from="weather.providers",
    ),
}


# ---------------------------------------------------------------------------
# Lazy item_model resolution  (avoids circular imports at module load time)
# ---------------------------------------------------------------------------

_item_models_resolved = False


def _ensure_item_models() -> None:
    """Populate ``item_model`` on any ``"items"`` hints that need it.

    Domain model imports are deferred to this function so that importing
    ``uihints`` early in the boot sequence does not trigger circular imports.
    """
    if UI_HINTS["pvforecast.planes"].item_model is None:
        from akkudoktoreos.prediction.pvforecast import (  # noqa: PLC0415
            PVForecastPlaneSetting,
        )

        UI_HINTS["pvforecast.planes"].item_model = PVForecastPlaneSetting

    if UI_HINTS["devices.batteries"].item_model is None:
        from akkudoktoreos.devices.settings.batterysettings import (
            BatteriesCommonSettings,
        )

        UI_HINTS["devices.batteries"].item_model = BatteriesCommonSettings

    if UI_HINTS["devices.electric_vehicles"].item_model is None:
        from akkudoktoreos.devices.settings.batterysettings import (
            BatteriesCommonSettings,
        )

        UI_HINTS["devices.electric_vehicles"].item_model = BatteriesCommonSettings

    if UI_HINTS["devices.home_appliances"].item_model is None:
        from akkudoktoreos.devices.settings.homeappliancesettings import (
            HomeApplianceCommonSettings,
        )

        UI_HINTS["devices.home_appliances"].item_model = HomeApplianceCommonSettings

    if UI_HINTS["devices.inverters"].item_model is None:
        from akkudoktoreos.devices.settings.invertersettings import (
            InverterCommonSettings,
        )

        UI_HINTS["devices.inverters"].item_model = InverterCommonSettings

    if UI_HINTS["adapter.control.controllers"].item_model is None:
        from akkudoktoreos.adapter.control import ControllerSettings

        UI_HINTS["adapter.control.controllers"].item_model = ControllerSettings


def resolve_item_model(hint: UiHint) -> Optional[Any]:
    """Return the ``item_model`` for an ``"items"`` hint, resolving lazily.

    Args:
        hint: A ``UiHint`` with ``form == "items"``.

    Returns:
        The model class or instance, or ``None`` if unset.
    """
    global _item_models_resolved
    if not _item_models_resolved:
        _ensure_item_models()
        _item_models_resolved = True
    return hint.item_model


def discriminated_item_models(hint: UiHint) -> dict[str, type[BaseModel]]:
    """Map each discriminator value of a ``"map_items"`` hint to its model class.

    Args:
        hint: A ``UiHint`` with ``item_discriminator`` set and a discriminated
            union of Pydantic models as ``item_model``.

    Returns:
        The model class for every value of the discriminator field, in the
        order of the union, e.g. ``{"sma:energy-meter": SmaEnergyMeterSettings}``.
        Empty when the hint has no discriminator.

    Raises:
        ValueError: If a union member is not a Pydantic model or its
            discriminator field is not a ``Literal`` of one string.
    """
    discriminator = hint.item_discriminator
    if discriminator is None:
        return {}
    union = resolve_item_model(hint)
    if get_origin(union) is Annotated:
        union = get_args(union)[0]
    members = get_args(union) if get_origin(union) is Union else (union,)

    models: dict[str, type[BaseModel]] = {}
    for member in members:
        if not isinstance(member, type) or not issubclass(member, BaseModel):
            raise ValueError(f"Union member {member!r} of {hint.item_path} is no Pydantic model.")
        # The discriminator is a Literal of one string, e.g. Literal["sma:energy-meter"]; it has
        # no default (see ControllerBaseSettings).
        annotation = member.model_fields[discriminator].annotation
        values = get_args(annotation) if get_origin(annotation) is Literal else ()
        if len(values) != 1 or not isinstance(values[0], str):
            raise ValueError(
                f"{member.__name__}.{discriminator} needs a Literal of one str, got {annotation!r}."
            )
        models[values[0]] = member
    return models


def resolve_entry_model(hint: UiHint, entry: Any) -> Optional[Any]:
    """Return the model of one ``"map_items"`` entry.

    Args:
        hint: A ``UiHint`` with ``form == "map_items"``.
        entry: The current value of the entry, a dict for valid entries.

    Returns:
        The model shared by all entries, or, with ``item_discriminator``, the
        model selected by the entry's discriminator value.  ``None`` if that
        value selects no model.
    """
    if hint.item_discriminator is None:
        return resolve_item_model(hint)
    entry_type = entry.get(hint.item_discriminator) if isinstance(entry, dict) else None
    if not isinstance(entry_type, str):
        return None
    return discriminated_item_models(hint).get(entry_type)


# ---------------------------------------------------------------------------
# Resolver
# ---------------------------------------------------------------------------


def resolve_form_factory(
    hint: UiHint,
    config_details: dict[str, dict],
    entry_name: Optional[str] = None,
) -> Optional[Callable]:
    """Materialise a ``UiHint`` into a concrete ``update_form_factory`` callable.

    For ``"items"`` hints this returns ``None`` — the caller must dispatch
    to ``ConfigItemsCard`` separately after checking ``hint.form == "items"``.
    For ``"text"`` this returns ``None`` — the caller uses the default
    plain-text input.  All other form types return a callable.

    Args:
        hint:
            The ``UiHint`` to materialise.
        config_details:
            The fully-resolved config detail dict from
            ``create_config_details()``.  Used to look up runtime option
            lists via ``options_from`` / ``param_from``.  For the field of
            an entry it must hold the details of that entry, too.
        entry_name:
            Dotted name of the ``"items"`` / ``"map_items"`` entry the field
            belongs to, e.g. ``"adapter.control.controllers.inverter1"``.
            Needed for ``options_from_entry``.

    Returns:
        A ``(config_name: str, value: str) -> Grid`` factory, or ``None``.
    """

    def _load_list(key: str) -> list[str]:
        try:
            result = json.loads(config_details[key]["value"])
        except Exception:
            return []
        if isinstance(result, dict):
            return list(result)
        return result if isinstance(result, list) else []

    def _load_options() -> list[str]:
        if hint.options_from_entry and entry_name:
            return _load_list(f"{entry_name}.{hint.options_from_entry}")
        if isinstance(hint.options_from, list):
            return sorted({option for path in hint.options_from for option in _load_list(path)})
        if hint.options_from:
            return _load_list(hint.options_from)
        return []

    if hint.form in ("text", "items", "map_items"):
        return None

    if hint.form == "select":
        options = _load_options()
        if not options:
            options = list(hint.options)
        if hint.append_none and "None" not in options:
            options.append("None")
        if not options:
            return None  # nothing to select from, let the user type the value
        return make_config_update_value_form(options)

    if hint.form == "select_list":
        options = _load_options()
        if not options:
            options = list(hint.options)
        return make_config_update_list_form(options, free_text=hint.free_text)

    if hint.form == "select_lazy":
        return make_config_update_lazy_select_form(hint.options_source or "")

    if hint.form == "map":
        available_values: Optional[list[str]] = None
        available_keys: Optional[list[str]] = None
        available_values = _load_options() or None
        if hint.param_from:
            available_keys = _load_list(hint.param_from) or None
        return make_config_update_map_form(available_keys, available_values)

    if hint.form == "time_windows":
        return make_config_update_time_windows_windows_form(
            value_description=hint.value_description,
        )

    return None  # unreachable for valid UiFormType values


# ---------------------------------------------------------------------------
# Suffix-based lookup for indexed sub-model fields
# ---------------------------------------------------------------------------


def hint_for_indexed_field(field_name: str, list_path: str) -> Optional[UiHint]:
    """Return the UiHint for a sub-field inside an 'items' or 'map_items' list.

    Strips the index segment (numeric for lists, any string for maps) from a
    dotted field name and looks up the canonical hint key.

    Args:
        field_name:
            Full dotted config name including the index, e.g.
            ``"pvforecast.planes.2.mountingplace"`` or
            ``"devices.home_appliances.dishwasher1.time_windows"``.
        list_path:
            The ``item_path`` from the parent ``UiHint``, e.g.
            ``"pvforecast.planes"`` or ``"devices.home_appliances"``.

    Returns:
        The matching ``UiHint``, or ``None`` if none is registered.
    """
    prefix = list_path + "."
    if not field_name.startswith(prefix):
        return None
    remainder = field_name[len(prefix) :]  # e.g. "2.mountingplace" or "dishwasher1.time_windows"
    parts = remainder.split(".", 1)
    if len(parts) < 2:
        return None
    # Accept both numeric (list) and string (map) index segments
    canonical = list_path + "." + parts[1]
    return UI_HINTS.get(canonical)


def optional_model_rows(
    config_details: dict[str, dict],
    hint_for: Callable[[str], Optional[UiHint]],
) -> tuple[set[str], set[str]]:
    """Find the rows of optional sub-models and the rows they hide.

    ``create_config_details`` marks the row of an optional sub-model field with
    ``"optional-model"`` (the model class).  Its sub-fields have rows of their own.

    Args:
        config_details: Configuration details, as created by ``create_config_details``.
        hint_for: Returns the ``UiHint`` of a row name, e.g. ``UI_HINTS.get`` or
            ``lambda name: hint_for_indexed_field(name, item_path)``.

    Returns:
        A tuple ``(switched, hidden)``: the names of the rows that get an on/off switch,
        and the names of the rows not to render, i.e. the sub-fields of switched rows that
        are off and the rows of optional sub-models whose hint opts out of the switch.
    """
    switched: set[str] = set()
    hidden: set[str] = set()
    off_prefixes: list[str] = []
    for name, details in config_details.items():
        if details.get("optional-model") is None:
            continue
        hint = hint_for(name)
        if hint is not None and not hint.optional_switch:
            hidden.add(name)
            continue
        switched.add(name)
        if not optional_model_is_on(details["value"]):
            off_prefixes.append(f"{name}.")
    if off_prefixes:
        hidden.update(name for name in config_details if name.startswith(tuple(off_prefixes)))
    return switched, hidden


def hint_for_plane_field(field_name: str) -> Optional[UiHint]:
    """Back-compat wrapper — prefer ``hint_for_indexed_field`` directly."""
    return hint_for_indexed_field(field_name, "pvforecast.planes")
