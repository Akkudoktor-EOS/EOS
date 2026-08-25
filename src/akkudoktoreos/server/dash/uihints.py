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
from typing import Any, Callable, Literal, Optional

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
            option list (JSON-encoded ``list[str]``).  Takes precedence
            over ``options`` when both are set.

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
            at first use to avoid circular imports.

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
    """

    form: UiFormType = "text"

    # select / select_list / map
    options: list[str] = field(default_factory=list)
    options_from: Optional[str] = None
    options_source: Optional[str] = None
    param_from: Optional[str] = None
    append_none: bool = False

    # time_windows
    value_description: Optional[str] = None

    # items
    item_model: Optional[Any] = None
    item_path: Optional[str] = None
    max_items_from: Optional[str] = None


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
    "devices.batteries": UiHint(
        form="items",
        item_path="devices.batteries",
    ),
    "devices.electric_vehicles": UiHint(
        form="items",
        item_path="devices.electric_vehicles",
    ),
    "devices.home_appliances": UiHint(
        form="items",
        item_path="devices.home_appliances",
    ),
    # Sub-field hint for the time_windows field inside each appliance entry
    "devices.home_appliances.cycle_time_windows.windows": UiHint(
        form="time_windows",
        value_description="cycle index (0-based)",
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
    "pvforecast.homeassistant.entity_id": UiHint(
        form="select",
        options_from="adapter.homeassistant.homeassistant_entity_ids",
    ),
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
        from akkudoktoreos.devices.devices import (
            BatteriesCommonSettings,
        )

        UI_HINTS["devices.batteries"].item_model = BatteriesCommonSettings

    if UI_HINTS["devices.electric_vehicles"].item_model is None:
        from akkudoktoreos.devices.devices import (
            BatteriesCommonSettings,
        )

        UI_HINTS["devices.electric_vehicles"].item_model = BatteriesCommonSettings

    if UI_HINTS["devices.home_appliances"].item_model is None:
        from akkudoktoreos.devices.devices import (
            HomeApplianceCommonSettings,
        )

        UI_HINTS["devices.home_appliances"].item_model = HomeApplianceCommonSettings


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


# ---------------------------------------------------------------------------
# Resolver
# ---------------------------------------------------------------------------


def resolve_form_factory(
    hint: UiHint,
    config_details: dict[str, dict],
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
            lists via ``options_from`` / ``param_from``.

    Returns:
        A ``(config_name: str, value: str) -> Grid`` factory, or ``None``.
    """

    def _load_list(key: str) -> list[str]:
        try:
            result = json.loads(config_details[key]["value"])
            return result if isinstance(result, list) else []
        except Exception:
            return []

    if hint.form in ("text", "items", "map_items"):
        return None

    if hint.form == "select":
        options: list[str] = []
        if hint.options_from:
            options = _load_list(hint.options_from)
        if not options:
            options = list(hint.options)
        if hint.append_none and "None" not in options:
            options.append("None")
        return make_config_update_value_form(options)

    if hint.form == "select_list":
        options = []
        if hint.options_from:
            options = _load_list(hint.options_from)
        if not options:
            options = list(hint.options)
        return make_config_update_list_form(options)

    if hint.form == "select_lazy":
        return make_config_update_lazy_select_form(hint.options_source or "")

    if hint.form == "map":
        available_values: Optional[list[str]] = None
        available_keys: Optional[list[str]] = None
        if hint.options_from:
            available_values = _load_list(hint.options_from) or None
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


def hint_for_plane_field(field_name: str) -> Optional[UiHint]:
    """Back-compat wrapper — prefer ``hint_for_indexed_field`` directly."""
    return hint_for_indexed_field(field_name, "pvforecast.planes")
