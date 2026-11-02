"""Observe JSONPath transformations without changing its navigation semantics."""

from collections.abc import Callable
from copy import copy
from dataclasses import dataclass
from typing import Any

from jsonpath_ng import DatumInContext, JSONPath
from jsonpath_ng.ext.arithmetic import Operation
from jsonpath_ng.ext.filter import Expression, Filter
from jsonpath_ng.ext.iterable import Len, Path, SortedThis
from jsonpath_ng.ext.string import Split, Str, Sub
from jsonpath_ng.jsonpath import Child, Descendants, Intersect, Union, Where

from tracecat.exceptions import TracecatExpressionError
from tracecat.secrets.masking import SecretMaskCollector


def _transform[T](run: Callable[[], T], *, sensitive: bool, operation: str) -> T:
    """Keep public diagnostics; sever partial secret diagnostics on failure."""
    try:
        return run()
    except Exception as exc:
        if not sensitive:
            raise
        error = TracecatExpressionError(
            f"{type(exc).__name__} in JSONPath {operation}(***)"
        )
    raise error


@dataclass(repr=False)
class _ObservedTransform(JSONPath):
    path: JSONPath
    masks: SecretMaskCollector

    def find(self, data: Any) -> list[DatumInContext]:
        datum = DatumInContext.wrap(data)
        # Path consumes metadata, not the selected object's value.
        source = str(datum.path) if isinstance(self.path, Path) else datum.value
        sensitive = self.masks.contains(source)
        matches = _transform(
            lambda: self.path.find(datum),
            sensitive=sensitive,
            operation=type(self.path).__name__,
        )
        if sensitive:
            for match in matches:
                self.masks.observe(match.value, include_keys=True)
        return matches


def _observe_operation(
    operation: Callable[[Any, Any], Any], masks: SecretMaskCollector
) -> Callable[[Any, Any], Any]:
    def observed(left: Any, right: Any) -> Any:
        # Arithmetic can select operands from different paths. Inspect those
        # actual operands, not the enclosing document and its public siblings.
        sensitive = masks.contains(left) or masks.contains(right)
        try:
            result = operation(left, right)
        except TypeError:
            # Operation.find handles this as an empty match. Preserve that
            # control flow instead of turning it into an evaluation failure.
            raise
        except Exception as exc:
            if not sensitive:
                raise
            error = TracecatExpressionError(
                f"{type(exc).__name__} in JSONPath arithmetic(***)"
            )
        else:
            if sensitive:
                masks.observe(result, include_keys=True)
            return result
        raise error

    return observed


def _observe_path(path: JSONPath, masks: SecretMaskCollector) -> JSONPath:
    """Copy composite nodes to instrument transforms in this evaluation only.

    Cached parser trees stay immutable. Navigation, filtering, recursion and
    ordering still execute in jsonpath_ng, once, with its original match context.
    """
    match path:
        case Child() | Descendants() | Where() | Union() | Intersect():
            left = _observe_path(path.left, masks)
            right = _observe_path(path.right, masks)
            if left is path.left and right is path.right:
                return path
            observed = copy(path)
            observed.left, observed.right = left, right
            return observed
        case Operation():
            observed = copy(path)
            if isinstance(path.left, JSONPath):
                observed.left = _observe_path(path.left, masks)
            if isinstance(path.right, JSONPath):
                observed.right = _observe_path(path.right, masks)
            observed.op = _observe_operation(path.op, masks)
            return observed
        case Filter():
            observed = copy(path)
            observed.expressions = [
                _observe_path(expr, masks) for expr in path.expressions
            ]
            return observed
        case Expression():
            target = _observe_path(path.target, masks)
            if target is path.target:
                return path
            observed = copy(path)
            observed.target = target
            return observed
        case SortedThis():
            observed = copy(path)
            if path.expressions:
                observed.expressions = [
                    (_observe_path(expr, masks), reverse)
                    for expr, reverse in path.expressions
                ]
            return observed
        case Split() | Sub() | Str() | Len() | Path():
            return _ObservedTransform(path, masks)
        case _:
            # Selection (including keys) and ordering retain scalar values;
            # only value-producing operations need to register new masks.
            return path


def find_with_secret_masks(
    path: JSONPath, data: Any, masks: SecretMaskCollector
) -> list[DatumInContext]:
    """Find matches, observing values produced by native JSONPath transforms."""
    if not masks.values:
        return path.find(data)
    return _observe_path(path, masks).find(data)
