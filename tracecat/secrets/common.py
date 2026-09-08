from __future__ import annotations

import re
import traceback
from collections.abc import Callable, Coroutine, Iterable, Mapping, Sequence
from dataclasses import dataclass
from typing import Any

from tracecat.exceptions import TracecatException
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


@dataclass(frozen=True, slots=True)
class CapturedFailure:
    """Where a masked exception originally failed, and what it was.

    Captured before the original traceback is dropped: frame objects retain
    their locals, which is where the resolved secret lives, so the traceback
    cannot be carried over just to keep the location.
    """

    original_type: str
    filename: str
    function: str
    lineno: int | None = None

    @classmethod
    def from_exc(cls, exc: BaseException) -> CapturedFailure:
        """Capture the last traceback frame, or an unknown site when there is none."""
        if frames := traceback.extract_tb(exc.__traceback__):
            last = frames[-1]
            return cls(
                original_type=type(exc).__name__,
                filename=last.filename,
                function=last.name,
                lineno=last.lineno,
            )
        return cls(
            original_type=type(exc).__name__,
            filename="<unknown>",
            function="<unknown>",
        )


class MaskedSecretError(TracecatException):
    """An error whose message has had secret values masked out."""

    captured: CapturedFailure | None = None
    """The original failure, or None when it could not be determined."""


def mask_exception(exc: BaseException, masks: Iterable[str]) -> Exception:
    """Rebuild an exception with secret values masked out of its message.

    Returns a plain wrapper rather than the original type: re-instantiating an
    arbitrary exception is not safe when its __init__ takes a custom signature.

    The returned object carries no plaintext: its message and detail are masked,
    and it holds neither the original's cause, context, nor traceback (frame
    objects retain their locals, which is where the resolved secret lives).

    It does NOT follow that the raised exception is safe for any renderer. Two
    things remain the caller's responsibility:

    - Raise it only AFTER the `except` block has exited, as
      `call_with_masked_errors` / `await_with_masked_errors` do. Python attaches
      the in-flight exception as `__context__` at raise time, so raising in
      place re-links the plaintext regardless of what this builds.
    - `raise` builds a NEW traceback rooted at the raising frame. That frame's
      locals typically still hold the resolved secrets, and Sentry defaults to
      include_local_variables=True, so it captures them. Masking cannot reach
      those; only disabling local capture can.

    The failing location is copied out first so callers keep it.
    """
    pattern = _compile_mask_pattern(masks)
    masked_message = _apply_mask_pattern(str(exc), pattern)
    detail = getattr(exc, "detail", None)
    masked = MaskedSecretError(masked_message)
    if detail is not None:
        masked.detail = _apply_masks_object(detail, pattern)
    if exc.__traceback__ is not None:
        masked.captured = CapturedFailure.from_exc(exc)
    return masked


def call_with_masked_errors[T](fn: Callable[[], T], *, masks: Iterable[str]) -> T:
    """Run ``fn``; any failure is re-raised as a masked copy with no chain.

    Owns the capture-then-raise contract for :func:`mask_exception`: Python
    attaches the in-flight exception as ``__context__`` at raise time (and
    ``raise ... from None`` clears only ``__cause__``), so the masked copy is
    built inside the handler but raised only after the handler has exited,
    leaving neither the plaintext exception nor its frame locals reachable
    through the chain.
    """
    try:
        return fn()
    except Exception as e:
        error = mask_exception(e, masks)
    raise error


async def await_with_masked_errors[T](
    coro: Coroutine[Any, Any, T], *, masks: Iterable[str]
) -> T:
    """Awaiting counterpart of :func:`call_with_masked_errors`."""
    try:
        return await coro
    except Exception as e:
        error = mask_exception(e, masks)
    raise error
