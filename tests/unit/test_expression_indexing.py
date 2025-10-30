from __future__ import annotations

import sys
import types

import pytest


def _install_loguru_stub() -> None:
    loguru_stub = types.ModuleType("loguru")

    class _Logger:
        def bind(self, **kwargs):  # pragma: no cover - stub
            return self

        def trace(self, *args, **kwargs):  # pragma: no cover - stub
            return None

        debug = info = success = warning = error = exception = critical = trace

        def remove(self, *args, **kwargs):  # pragma: no cover - stub
            return None

        def add(self, *args, **kwargs):  # pragma: no cover - stub
            return 0

    loguru_stub.logger = _Logger()
    sys.modules.setdefault("loguru", loguru_stub)


def _install_jsonpath_stub() -> None:
    jsonpath_stub = types.ModuleType("jsonpath_ng")
    ext_module = types.ModuleType("jsonpath_ng.ext")

    class _JSONPathExpression:
        def __init__(self, expr: str) -> None:
            self.expr = expr

        def find(self, _operand):  # pragma: no cover - stub
            return []

    def parse(expr: str) -> _JSONPathExpression:  # pragma: no cover - stub
        return _JSONPathExpression(expr)

    ext_module.parse = parse
    jsonpath_stub.ext = ext_module
    sys.modules.setdefault("jsonpath_ng", jsonpath_stub)
    sys.modules.setdefault("jsonpath_ng.ext", ext_module)

    exceptions_module = types.ModuleType("jsonpath_ng.exceptions")

    class JsonPathParserError(Exception):
        pass

    class JSONPathError(Exception):
        pass

    exceptions_module.JsonPathParserError = JsonPathParserError
    exceptions_module.JSONPathError = JSONPathError
    sys.modules.setdefault("jsonpath_ng.exceptions", exceptions_module)


def _install_lark_stub() -> None:
    lark_stub = types.ModuleType("lark")

    class VisitError(Exception):
        def __init__(self, obj=None, orig_exc=None):
            super().__init__(str(orig_exc) if orig_exc else "visit error")
            self.obj = obj
            self.orig_exc = orig_exc or self

    class Token(str):
        def __new__(cls, value: str, type_: str | None = None):
            inst = super().__new__(cls, value)
            inst.value = value
            inst.type = type_
            return inst

    class Tree(list):
        def __init__(self, data: str, children=None):
            super().__init__(children or [])
            self.data = data

        def __class_getitem__(cls, _item):
            return cls

    class Transformer:
        def transform(self, tree):  # pragma: no cover - stub
            raise NotImplementedError

    class Visitor:
        def visit(self, tree):  # pragma: no cover - stub
            return tree

    def v_args(**_kwargs):
        def decorator(func):
            return func

        return decorator

    lark_stub.VisitError = VisitError
    lark_stub.Token = Token
    lark_stub.Tree = Tree
    lark_stub.Transformer = Transformer
    lark_stub.Visitor = Visitor
    lark_stub.v_args = v_args
    sys.modules.setdefault("lark", lark_stub)

    exceptions_module = types.ModuleType("lark.exceptions")
    exceptions_module.VisitError = VisitError
    sys.modules.setdefault("lark.exceptions", exceptions_module)


def _install_expr_functions_stub() -> None:
    functions_stub = types.ModuleType("tracecat.expressions.functions")
    functions_stub.cast = lambda value, typename: value
    functions_stub.FUNCTION_MAPPING = {}
    functions_stub.OPERATORS = {}
    sys.modules.setdefault("tracecat.expressions.functions", functions_stub)


_install_loguru_stub()
_install_jsonpath_stub()
_install_lark_stub()
_install_expr_functions_stub()

from tracecat.expressions.parser.evaluator import ExprEvaluator
from tracecat.types.exceptions import TracecatExpressionError


@pytest.fixture
def evaluator() -> ExprEvaluator:
    return ExprEvaluator()


@pytest.mark.parametrize(
    "base,indexes,expected",
    [
        (["hello", "world"], (1,), "world"),
        ((1, 2, 3), (0,), 1),
        ("tracecat", (-1,), "t"),
        (([["nested"]], 1), (0, 0, 0), "nested"),
        ({"foo": {"bar": 42}}, ("foo", "bar"), 42),
    ],
)
def test_primary_expr_indexing(
    evaluator: ExprEvaluator, base, indexes, expected
) -> None:
    assert evaluator.primary_expr(base, *indexes) == expected


@pytest.mark.parametrize(
    "base,indexes,error_message",
    [
        ([1, 2], (5,), "Sequence index 5 out of range"),
        ([1, 2], ("0",), "Sequence indices must be integers"),
        (42, (0,), "Object of type 'int' is not indexable"),
        ({"foo": 1}, ("bar",), "Key 'bar' not found for mapping access"),
    ],
)
def test_primary_expr_indexing_errors(
    evaluator: ExprEvaluator, base, indexes, error_message: str
) -> None:
    with pytest.raises(TracecatExpressionError) as exc:
        evaluator.primary_expr(base, *indexes)
    assert error_message in str(exc.value)
