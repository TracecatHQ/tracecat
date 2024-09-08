import re
from collections.abc import Iterable

from tracecat.secrets.constants import MASK_VALUE


def apply_masks(value: str, masks: Iterable[str]) -> str:
    if not masks:
        return value

    pattern = "|".join(map(re.escape, masks))
    return re.sub(pattern, MASK_VALUE, value)


def apply_masks_object[T](obj: T, masks: Iterable[str]) -> T:
    """Process jsonpaths in strings, lists, and dictionaries."""
    match obj:
        case str():
            return apply_masks(obj, masks)
        case list():
            return [apply_masks_object(item, masks) for item in obj]
        case dict():
            return {k: apply_masks_object(v, masks) for k, v in obj.items()}
        case _:
            return obj
