import re
from collections.abc import Iterable, Mapping, Sequence

from tracecat.secrets.constants import MASK_VALUE


def _compile_mask_pattern(masks: Iterable[str]) -> re.Pattern[str] | None:
    """Compile a reusable pattern for the provided secret values."""
    # Filter out single-character masks to prevent over-aggressive masking.
    filtered_masks = [mask for mask in masks if len(mask) > 1]
    if not filtered_masks:
        return None

    # Sort longest-first so a longer secret is not partially matched by a
    # shorter substring that happens to appear earlier in the alternation.
    filtered_masks.sort(key=len, reverse=True)
    return re.compile("|".join(map(re.escape, filtered_masks)))


def _apply_mask_pattern(value: str, pattern: re.Pattern[str] | None) -> str:
    if pattern is None:
        return value
    return pattern.sub(MASK_VALUE, value)


def apply_masks(value: str, masks: Iterable[str]) -> str:
    return _apply_mask_pattern(value, _compile_mask_pattern(masks))


def _apply_masks_object[T](obj: T, pattern: re.Pattern[str] | None) -> T:
    match obj:
        case str():
            return _apply_mask_pattern(obj, pattern)
        case Sequence():
            return type(obj)(_apply_masks_object(item, pattern) for item in obj)  # pyright: ignore[reportCallIssue]
        case Mapping():
            masked_items = (
                (k, _apply_masks_object(v, pattern)) for k, v in obj.items()
            )
            return type(obj)(masked_items)  # pyright: ignore[reportCallIssue]
        case _:
            return obj


def apply_masks_object[T](obj: T, masks: Iterable[str]) -> T:
    """Mask secret values in strings, sequences, and mappings."""
    return _apply_masks_object(obj, _compile_mask_pattern(masks))
