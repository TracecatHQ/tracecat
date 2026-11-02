"""Runtime values observed at secret-dependent expression nodes.

The collection lives only for an invocation. It is never persisted with workflow
history or sent to telemetry. Secret-dependent opaque action results are
registered by the executor; transformations in code that fails before returning
are not observable here.
"""

from __future__ import annotations

import json
import re
from collections.abc import Mapping, Set
from dataclasses import dataclass, field
from typing import Any

from tracecat.secrets.common import _apply_masks_object, _compile_mask_pattern
from tracecat.secrets.matcher import SecretSubstringIndex


@dataclass(slots=True, repr=False)
class SecretMaskCollector:
    """Collect concrete sensitive values and their diagnostic representations."""

    _values: set[str] = field(default_factory=set)
    _observed_strings: set[str] = field(default_factory=set)
    _index: SecretSubstringIndex = field(default_factory=SecretSubstringIndex)
    _pattern: re.Pattern[str] | None = None
    _pattern_dirty: bool = False

    @property
    def values(self) -> Set[str]:
        """Read-only view of collected representations for subprocess transport."""
        return self._values

    def _add(self, value: str) -> None:
        if value and value not in self._values:
            self._values.add(value)
            self._index.add(value)
            self._pattern_dirty = True

    def observe(self, value: Any, *, include_keys: bool = False) -> None:
        """Register values, including keys only for secret-derived containers."""
        if isinstance(value, Mapping):
            for key, item in value.items():
                if include_keys:
                    self.observe(key, include_keys=True)
                self.observe(item, include_keys=include_keys)
        elif isinstance(value, (list, tuple)):
            for item in value:
                self.observe(item, include_keys=include_keys)
        elif isinstance(value, (str, bytes, int, float, bool)):
            text = str(value)
            if not text:
                return
            self._add(text)
            if isinstance(value, str) and value not in self._observed_strings:
                self._observed_strings.add(value)
                # Exceptions use both Python repr and JSON escaping. Keep the
                # unquoted interiors as they can be embedded in larger strings.
                self._add(repr(value)[1:-1])
                self._add(ascii(value)[1:-1])
                for ensure_ascii in (False, True):
                    self._add(json.dumps(value, ensure_ascii=ensure_ascii)[1:-1])

    def contains(self, value: Any) -> bool:
        """Whether a value or container contains a known secret representation."""
        if isinstance(value, Mapping):
            return any(
                self.contains(key) or self.contains(item) for key, item in value.items()
            )
        if isinstance(value, (list, tuple)):
            return any(self.contains(item) for item in value)
        if not isinstance(value, (str, bytes, int, float, bool)):
            return False
        text = str(value)
        return text in self._values or self._index.contains(text)

    def redact[T](self, value: T) -> T:
        """Mask strings in a diagnostic, including dictionary keys."""
        if self._pattern_dirty:
            self._pattern = _compile_mask_pattern(self._values, include_short=True)
            self._pattern_dirty = False
        return _apply_masks_object(value, self._pattern, mask_keys=True)
