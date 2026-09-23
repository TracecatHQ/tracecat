"""Runtime values observed at secret-dependent expression nodes.

The collection lives only for an invocation. It is never persisted with workflow
history or sent to telemetry. Transformations inside opaque action code are not
observable here.
"""

from __future__ import annotations

import json
from collections.abc import Mapping
from dataclasses import dataclass, field
from typing import Any

from tracecat.secrets.common import apply_masks_object


@dataclass(slots=True, repr=False)
class SecretMaskCollector:
    """Collect concrete sensitive values and their diagnostic representations."""

    values: set[str] = field(default_factory=set, repr=False)

    def observe(self, value: Any) -> None:
        """Register scalar values without stringifying arbitrary objects."""
        if isinstance(value, Mapping):
            for item in value.values():
                self.observe(item)
        elif isinstance(value, (list, tuple)):
            for item in value:
                self.observe(item)
        elif isinstance(value, (str, bytes, int, float, bool)):
            text = str(value)
            if not text:
                return
            self.values.add(text)
            if isinstance(value, str):
                # Exceptions use both Python repr and JSON escaping. Keep the
                # unquoted interiors as they can be embedded in larger strings.
                self.values.update((repr(value)[1:-1], ascii(value)[1:-1]))
                for ensure_ascii in (False, True):
                    self.values.add(json.dumps(value, ensure_ascii=ensure_ascii)[1:-1])

    def contains(self, value: Any) -> bool:
        """Whether a scalar contains a known representation of a secret."""
        if not isinstance(value, (str, bytes, int, float, bool)):
            return False
        text = str(value)
        return any(candidate in text for candidate in self.values)

    def redact[T](self, value: T) -> T:
        """Mask strings in a diagnostic, including dictionary keys."""
        return apply_masks_object(
            value, self.values, include_short=True, mask_keys=True
        )
