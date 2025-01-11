from __future__ import annotations

from contextvars import ContextVar

import loguru

from tracecat.dsl.models import RunContext
from tracecat.types.auth import Role

ctx_run: ContextVar[RunContext] = ContextVar("run", default=None)
ctx_role: ContextVar[Role] = ContextVar("role", default=None)
ctx_logger: ContextVar[loguru.Logger] = ContextVar("logger", default=None)

SecretContextEnv = dict[str, dict[str, str]]
ctx_env: ContextVar[SecretContextEnv] = ContextVar("env", default={})
