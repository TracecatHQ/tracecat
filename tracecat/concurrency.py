from collections.abc import Callable
from concurrent.futures import ProcessPoolExecutor
from typing import Any, ParamSpec, override

import cloudpickle

from tracecat.auth import Role
from tracecat.contexts import ctx_session_role
from tracecat.logger import standard_logger

logger = standard_logger(__name__)

_P = ParamSpec("_P")


def _run_serialized_fn(serialized_wrapped_fn: bytes, role: Role, /, *args, **kwargs):
    # NOTE: This is not the raw function - it is still wrapped by the `wrapper` decorator
    wrapped_fn: Callable[_P, Any] = cloudpickle.loads(serialized_wrapped_fn)
    ctx_session_role.set(role)
    logger.debug(f"{role=}")
    kwargs["__role"] = role
    res = wrapped_fn(*args, **kwargs)
    return res


class CloudpickleProcessPoolExecutor(ProcessPoolExecutor):
    @override
    def submit(self, fn: Callable[_P, Any], /, *args, **kwargs):
        # We need to pass the role to the function running in the child process
        role = ctx_session_role.get()
        logger.debug(f"{role=}")
        serialized_fn = cloudpickle.dumps(fn)
        return super().submit(_run_serialized_fn, serialized_fn, role, *args, **kwargs)
