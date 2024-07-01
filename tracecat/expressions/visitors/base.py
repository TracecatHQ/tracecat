from abc import ABC, abstractmethod
from collections.abc import Iterable
from typing import Any

from tracecat.expressions import functions
from tracecat.types.exceptions import TracecatExpressionError


class ExprVisitor(ABC):
    @abstractmethod
    def visit_action_expr(self, expr: str) -> Any:
        pass

    @abstractmethod
    def visit_secret_expr(self, expr: str) -> Any:
        pass

    @abstractmethod
    def visit_function_expr(self, expr: str, fn_name: str, fn_args: list[Any]) -> Any:
        pass

    @abstractmethod
    def visit_input_expr(self, expr: str) -> Any:
        pass

    @abstractmethod
    def visit_trigger_expr(self, expr: str) -> Any:
        pass

    @abstractmethod
    def visit_iterator_expr(self, iter_var_expr: str, collection: Iterable[Any]) -> Any:
        pass

    @abstractmethod
    def visit_ternary_expr(
        self, cond_expr: str, true_expr: str, false_expr: str
    ) -> Any:
        pass

    @abstractmethod
    def visit_cast_expr(self, expr: str, typename: str) -> Any:
        pass

    @abstractmethod
    def visit_literal_expr(self, expr: str) -> Any:
        pass

    @abstractmethod
    def visit_env_expr(self, expr: str) -> Any:
        pass

    @abstractmethod
    def visit_local_vars_expr(self, expr: str) -> Any:
        pass

    def visit_trailing_cast_expr(self, expr_result: Any, rtype: str) -> Any:
        return functions.cast(expr_result, rtype)

    def handle_error(self, expr: str) -> Any:
        raise TracecatExpressionError(f"Invalid expression: {expr!r}")
