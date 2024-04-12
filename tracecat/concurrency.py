from collections.abc import Callable
from concurrent.futures import ProcessPoolExecutor
from typing import Any, ParamSpec, override

import cloudpickle

from tracecat.auth import Role
from tracecat.contexts import ctx_session_role
from tracecat.logger import standard_logger

logger = standard_logger(__name__)

_P = ParamSpec("_P")


def _run_serialized_fn(serialized_fn: bytes, role: Role, /, *args, **kwargs):
    fn: Callable[_P, Any] = cloudpickle.loads(serialized_fn)
    ctx_session_role.set(role)
    logger.debug(f"{role=}")
    return fn(*args, **kwargs)


class CloudpickleProcessPoolExecutor(ProcessPoolExecutor):
    @override
    def submit(self, fn: Callable[_P, Any], /, *args, **kwargs):
        # We need to pass the role to the function running in the child process
        role = ctx_session_role.get()
        logger.debug(f"{role=}")
        serialized_fn = cloudpickle.dumps(fn)
        return super().submit(_run_serialized_fn, serialized_fn, role, *args, **kwargs)
