from __future__ import annotations

import logging
from contextvars import ContextVar
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from tracecat.auth import Role


ctx_role: ContextVar[Role] = ContextVar("role", default=None)
ctx_logger: ContextVar[logging.Logger] = ContextVar("logger", default=None)
