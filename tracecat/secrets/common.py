import re
from collections.abc import Iterable, Mapping, Sequence

from tracecat.secrets.constants import MASK_VALUE


def apply_masks(value: str, masks: Iterable[str]) -> str:
    if not masks:
        return value

    # Filter out single-character masks to prevent over-aggressive masking
    filtered_masks = [mask for mask in masks if len(mask) > 1]

    if not filtered_masks:
        return value

    pattern = "|".join(map(re.escape, filtered_masks))
    return re.sub(pattern, MASK_VALUE, value)


def apply_masks_object[T](obj: T, masks: Iterable[str]) -> T:
    """Process jsonpaths in strings, sequences, and mappings."""
    match obj:
        case str():
            return apply_masks(obj, masks)
        case Sequence():
            return type(obj)(apply_masks_object(item, masks) for item in obj)  # type: ignore
        case Mapping():
            return type(obj)((k, apply_masks_object(v, masks)) for k, v in obj.items())  # type: ignore
        case _:
            return obj
