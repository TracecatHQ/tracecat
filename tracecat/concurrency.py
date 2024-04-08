from collections.abc import Callable
from concurrent.futures import ProcessPoolExecutor
from typing import Any, override

import cloudpickle


def _apply_cloudpickle(fn: Callable[..., Any], /, *args, **kwargs):
    fn = cloudpickle.loads(fn)
    return fn(*args, **kwargs)


class CloudpickleProcessPoolExecutor(ProcessPoolExecutor):
    @override
    def submit(self, fn: Callable[..., Any], /, *args, **kwargs):
        return super().submit(
            _apply_cloudpickle, cloudpickle.dumps(fn), *args, **kwargs
        )
