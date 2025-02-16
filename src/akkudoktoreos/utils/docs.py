from typing import Any

from pydantic.fields import FieldInfo

from akkudoktoreos.core.pydantic import PydanticBaseModel


def get_example_or_default(field_name: str, field_info: FieldInfo, example_ix: int) -> Any:
    """Generate a default value for a field, considering constraints."""
    if field_info.examples is not None:
        try:
            return field_info.examples[example_ix]
        except IndexError:
            return field_info.examples[-1]

    if field_info.default is not None:
        return field_info.default

    raise NotImplementedError(f"No default or example provided '{field_name}': {field_info}")


def get_model_structure_from_examples(
    model_class: type[PydanticBaseModel], multiple: bool
) -> list[dict[str, Any]]:
    """Create a model instance with default or example values, respecting constraints."""
    example_max_length = 1

    # Get first field with examples (non-default) to get example_max_length
    if multiple:
        for _, field_info in model_class.model_fields.items():
            if field_info.examples is not None:
                example_max_length = len(field_info.examples)
                break

    example_data: list[dict[str, Any]] = [{} for _ in range(example_max_length)]

    for field_name, field_info in model_class.model_fields.items():
        for example_ix in range(example_max_length):
            example_data[example_ix][field_name] = get_example_or_default(
                field_name, field_info, example_ix
            )
    return example_data
