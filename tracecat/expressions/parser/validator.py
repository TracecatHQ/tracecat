import re
from collections.abc import Awaitable, Iterator
from itertools import chain
from typing import Any, Literal, TypeVar, override

import jsonpath_ng
import jsonpath_ng.exceptions
from lark import Tree, Visitor, v_args
from lark.exceptions import VisitError
from pydantic import BaseModel, Field

from tracecat.concurrency import GatheringTaskGroup
from tracecat.dsl.models import DSLNodeResult
from tracecat.expressions import functions
from tracecat.expressions.shared import ExprContext, ExprType
from tracecat.logging import logger
from tracecat.types.exceptions import TracecatExpressionError
from tracecat.types.validation import ExprValidationResult

T = TypeVar("T")


class ExprValidationContext(BaseModel):
    """Container for the validation context of an expression tree."""

    action_refs: set[str]
    inputs_context: Any = Field(default_factory=dict)
    trigger_context: Any = Field(default_factory=dict)


class ExprValidator(Visitor):
    """Validate the expression tree by visiting each node and returning the result."""

    _visitor_name = "ExprValidator"

    def __init__(
        self,
        task_group: GatheringTaskGroup,
        validation_context: ExprValidationContext,
        validators: dict[ExprType, Awaitable[ExprValidationResult]] | None = None,
        *,
        strict: bool = True,
    ) -> None:
        self._task_group = task_group
        # Contextual information
        self._context = validation_context
        self._results: list[ExprValidationResult] = []
        self._strict = strict

        # External validators
        self._validators = validators or {}

        self.logger = logger.bind(visitor=self._visitor_name)

    """Utility"""

    def add(
        self,
        status: Literal["success", "error", "pending"],
        msg: str = "",
        type: ExprType = ExprType.GENERIC,
    ) -> None:
        self._results.append(
            ExprValidationResult(status=status, msg=msg, expression_type=type)
        )

    def results(self) -> Iterator[ExprValidationResult]:
        """Return all validation results."""
        yield from chain(self._task_group.results(), self._results)

    def errors(self) -> list[ExprValidationResult]:
        """Return all validation errors."""
        return [res for res in self.results() if res.status == "error"]

    @override
    def visit(self, tree: Tree) -> Any:
        try:
            return super().visit(tree)
        except VisitError as e:
            self.handle_error(str(e))

    def handle_error(self, expr: str) -> Any:
        self.add(
            status="error",
            msg=f"Invalid expression: {expr!r}",
            type=ExprType.GENERIC,
        )

    """Visitors"""

    def root(self, node: Tree):
        self.logger.trace("Visiting root:", node=node)

    def trailing_typecast_expression(self, node: Tree):
        _, typename = node.children
        self.logger.trace("Visit trailing cast expression", typename=typename)
        if typename not in functions.BUILTIN_TYPE_MAPPING:
            self.add(
                status="error",
                msg=f"Invalid type {typename!r} in trailing cast expression."
                f" Valid types are {list(functions.BUILTIN_TYPE_MAPPING.keys())}",
                type=ExprType.TYPECAST,
            )
        else:
            self.add(status="success", type=ExprType.TYPECAST)

    def actions(self, node: Tree):
        self.logger.trace("Visit action expression", node=node)
        jsonpath = get_jsonpath_body_from_context(node).lstrip(".")
        # ACTIONS.<ref>.<prop> [INDEX] [ATTRIBUTE ACCESS]
        ref, prop, *_ = jsonpath.split(".")
        if ref not in self._context.action_refs:
            self.add(
                status="error",
                msg=f"Invalid action reference {ref!r} in ACTION expression {jsonpath!r}",
                type=ExprType.ACTION,
            )
        # Check prop
        valid_properties = "|".join(DSLNodeResult.__annotations__.keys())
        pattern = rf"({valid_properties})(\[(\d+|\*)\])?"  # e.g. "result[0], result[*], result"
        if not re.match(pattern, prop):
            self.add(
                status="error",
                msg=f"Invalid property {prop!r} for action reference {ref!r} in ACTION expression {jsonpath!r}."
                f" Use one of {valid_properties}, e.g. `{ref}.{valid_properties[0]}`",
                type=ExprType.ACTION,
            )
        else:
            self.add(status="success", type=ExprType.ACTION)

    def secrets(self, node: Tree):
        self.logger.trace("Visit secret expression", expr=node)

        expr = node.children[0]
        try:
            sec_path = expr.lstrip(".")
            name, key = sec_path.split(".")
        except ValueError:
            sec_jsonpath = ExprContext.SECRETS + expr
            return self.add(
                status="error",
                msg=f"Invalid secret usage: {sec_jsonpath!r}. Must be in the format `SECRETS.my_secret.KEY`",
                type=ExprType.SECRET,
            )

        coro = self._validators[ExprType.SECRET](name, key)
        self._task_group.create_task(coro)

    def inputs(self, node: Tree):
        self.logger.trace("Visit input expression", node=node)
        jsonpath = get_jsonpath_body_from_context(node).lstrip(".")
        try:
            functions.eval_jsonpath(
                jsonpath,
                self._context.inputs_context,
                context_type=ExprContext.INPUTS,
                strict=self._strict,
            )
            self.add(status="success", type=ExprType.INPUT)
        except TracecatExpressionError as e:
            return self.add(status="error", msg=str(e), type=ExprType.INPUT)

    def trigger(self, node: Tree):
        self.logger.trace("Visit trigger expression", node=node)
        self.add(status="success", type=ExprType.TRIGGER)

    def env(self, node: Tree):
        self.logger.trace("Visit env expression", node=node)
        self.add(status="success", type=ExprType.ENV)

    def local_vars(self, node: Tree):
        self.logger.trace("Visit local vars expression", node=node)
        self.add(status="success", type=ExprType.LOCAL_VARS)

    def function(self, node: Tree):
        fn_name = node.children[0]
        is_mapped = fn_name.endswith(".map")
        fn_name = fn_name.rsplit(".", 1)[0] if is_mapped else fn_name
        self.logger.trace(
            "Visit function expression",
            fn_name=node,
            is_mapped=is_mapped,
            node=node,
        )

        if fn_name not in functions.FUNCTION_MAPPING:
            self.add(
                status="error",
                msg=f"Unknown function name {str(fn_name)!r} ({is_mapped=})",
                type=ExprType.FUNCTION,
            )
        else:
            self.add(status="success", type=ExprType.FUNCTION)

    def iterator(self, node: Tree):
        iter_var_expr, collection, *_ = node.children
        self.logger.trace(
            "Visit iterator expression",
            iter_var_expr=iter_var_expr,
            collection=collection,
        )
        if not re.match(r"^var\.", iter_var_expr):
            self.add(
                status="error",
                msg=f"Invalid iterator variable: {iter_var_expr!r}."
                " Please use `var.your.variable`",
                type=ExprType.ITERATOR,
            )
        elif not hasattr(collection, "__iter__"):
            self.add(
                status="error",
                msg=f"Invalid iterator collection: {collection!r}. Must be an iterable.",
                type=ExprType.ITERATOR,
            )
        else:
            self.add(status="success", type=ExprType.ITERATOR)

    def ternary(self, node: Tree):
        cond_expr, true_expr, false_expr = node.children
        self.logger.trace(
            "Visit ternary expression",
            cond_expr=cond_expr,
            true_expr=true_expr,
            false_expr=false_expr,
        )
        self.add(status="success", type=ExprType.TERNARY)

    def typecast(self, node: Tree):
        self.logger.trace("Visit typecast expression")
        typename, *children = node.children
        if typename not in functions.BUILTIN_TYPE_MAPPING:
            return self.add(
                status="error",
                msg=f"Invalid type {typename!r}."
                f" Valid types are {list(functions.BUILTIN_TYPE_MAPPING.keys())}",
                type=ExprType.TYPECAST,
            )

        self.logger.warning("Typecast expression", typename=typename, children=children)
        # If the child is a literal, we can typecast it directly
        child = children[0]
        if child.data == "literal":
            try:
                functions.cast(child.children[0], typename)
            except ValueError as e:
                self.add(
                    status="error",
                    msg=str(e),
                    type=ExprType.TYPECAST,
                )
        else:
            self.add(status="success", type=ExprType.TYPECAST)

    def literal(self, node: Tree):
        self.logger.trace("Visit literal expression", value=node.children[0])
        self.add(status="success", type=ExprType.LITERAL)

    def jsonpath_expression(self, node: Tree):
        self.logger.trace("Visiting jsonpath expression", children=node.children)
        try:
            combined_segments = "".join(node.children)
        except (AttributeError, ValueError) as e:
            self.logger.error("Invalid jsonpath segments", error=str(e))
            self.add(
                status="error",
                msg="Couldn't combine jsonpath expression segments: " + str(e),
            )
        try:
            jsonpath_ng.parse("$" + combined_segments)
        except jsonpath_ng.exceptions.JSONPathError as e:
            self.logger.error("Invalid jsonpath body", error=str(e))
            self.add(
                status="error",
                msg=str(e),
            )

    @v_args(inline=True)
    def jsonpath_segment(self, *args):
        self.logger.trace("Visiting jsonpath segment", args=args)


def get_jsonpath_body_from_context(node: Tree) -> str:
    """NOTE: This only is determinstic because our grammar doesn't inline 'jsonpath_expression'"""
    jsonpath_node = node.children[0]
    return "".join(jsonpath_node.children)
