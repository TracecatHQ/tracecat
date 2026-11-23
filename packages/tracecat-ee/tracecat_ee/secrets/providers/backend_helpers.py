"""Fetch-outcome and config helpers shared by secret store backends."""

from __future__ import annotations

import re
from collections.abc import Sequence
from typing import Final

from pydantic import BaseModel

from tracecat.secrets.types import Failure, FetchOutcome

VENDOR_CODE: Final = re.compile(r"[A-Za-z0-9_.:-]{1,64}")
"""A short vendor identifier (error code, HTTP status); never free text."""


def fail_all(references: Sequence[str], failure: Failure) -> dict[str, FetchOutcome]:
    """The same failure for every reference."""
    return dict.fromkeys(references, failure)


def apply_config_update[ConfigT: BaseModel](
    config: ConfigT, params: BaseModel
) -> ConfigT:
    """Apply the fields a client set, never ``provider`` and never a null."""
    return config.model_copy(
        update=params.model_dump(
            exclude_unset=True, exclude_none=True, exclude={"provider"}
        )
    )
