"""Shared helpers for list- and map-of-sub-model configuration cards.

Used by both ``itemscard.py`` (``list[PydanticSubModel]`` fields) and
``mapcard.py`` (``dict[str, PydanticSubModel]`` fields) to avoid duplicating
the Pydantic-introspection and required-field-collection logic between the
two card types. Free of imports from ``configuration.py``, ``itemscard.py``,
or ``mapcard.py`` to avoid circular dependencies.
"""

from typing import Any, cast

from monsterui.franken import Div, Input, P
from pydantic import BaseModel
from pydantic_core import PydanticUndefined


def resolve_model_cls(item_model: Any) -> type[BaseModel]:
    """Resolve a Pydantic model class from either a class or an instance.

    Args:
        item_model: A Pydantic model class or an instance of one.

    Returns:
        The model class, cast for mypy's benefit (``isinstance(x, type)``
        alone narrows to plain ``type``, not ``type[BaseModel]``).
    """
    if isinstance(item_model, type):
        return cast(type[BaseModel], item_model)
    return cast(type[BaseModel], type(item_model))


def item_model_defaults(item_model: Any) -> tuple[dict, list[str]]:
    """Build defaults for a Pydantic sub-model and report required-but-unset fields.

    Constructs a model instance using only fields that have defaults (either
    ``default`` or ``default_factory``), then serialises via
    ``model_dump(mode="json")`` to produce a fully JSON-safe dict. Fields
    without any default are reported separately rather than silently
    omitted — a freshly-constructed instance missing them would fail the
    model's own validation (e.g. ``consumption_wh``/``duration_h`` on
    ``HomeApplianceCommonSettings``), so callers must collect values for
    them before persisting a new item or entry.

    Args:
        item_model: A Pydantic model class or instance whose ``model_fields``
            will be inspected.

    Returns:
        A tuple of ``(defaults, required_missing)`` where ``defaults``
        contains every field that has a ``default`` or ``default_factory``,
        JSON-safe and ready to serialise, and ``required_missing`` lists the
        field names that have neither.
    """
    model_cls = resolve_model_cls(item_model)

    kwargs: dict[str, Any] = {}
    required_missing: list[str] = []
    for field_name, field_info in model_cls.model_fields.items():
        if field_info.default is not PydanticUndefined:
            kwargs[field_name] = field_info.default
        elif field_info.default_factory is not None:
            kwargs[field_name] = field_info.default_factory()
        else:
            required_missing.append(field_name)

    instance = model_cls.model_construct(**kwargs)
    defaults = instance.model_dump(mode="json", exclude_unset=True)
    return defaults, required_missing


def required_field_inputs(
    item_model: Any,
    required_missing: list[str],
    id_prefix: str,
) -> tuple[list[Div], list[str]]:
    """Build labelled inputs for a sub-model's required-but-undefaulted fields.

    Produces one ``Div``-wrapped ``Input`` per field in ``required_missing``,
    each carrying HTML ``required`` so the browser blocks form submission
    until every field has a value, plus the corresponding JS expressions for
    reading those inputs back out at submit time.

    Args:
        item_model: The Pydantic model class or instance the fields belong
            to, used to pick ``type="number"`` vs ``type="text"``.
        required_missing: Field names with no default, as returned by
            ``item_model_defaults``.
        id_prefix: A CSS/DOM-safe prefix (e.g. derived from the config name)
            used to build unique element ids for each input.

    Returns:
        A tuple of ``(inputs, js_pairs)`` where ``inputs`` is the list of
        rendered ``Div`` components to place in the form, and ``js_pairs``
        is a list of ``'"field_name": <js expression>'`` strings suitable
        for splicing into a JS object literal that reads the DOM values.
    """
    model_cls = resolve_model_cls(item_model)

    inputs: list[Div] = []
    js_pairs: list[str] = []
    for field_name in required_missing:
        field_id = f"{id_prefix}-{field_name}".replace(".", "-").replace("_", "-")
        annotation = model_cls.model_fields[field_name].annotation
        is_numeric = annotation in (int, float)
        inputs.append(
            Div(
                P(field_name, cls="text-xs text-muted-foreground"),
                Input(
                    id=field_id,
                    type="number" if is_numeric else "text",
                    required=True,
                    placeholder=field_name,
                ),
            )
        )
        value_expr = (
            f'Number(document.getElementById("{field_id}").value)'
            if is_numeric
            else f'document.getElementById("{field_id}").value'
        )
        js_pairs.append(f'"{field_name}": {value_expr}')

    return inputs, js_pairs
