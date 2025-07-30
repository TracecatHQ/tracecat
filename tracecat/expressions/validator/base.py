import inspect
from collections.abc import Awaitable, Callable, Iterator
from typing import Any, Literal, override

import jsonpath_ng.exceptions
import jsonpath_ng.ext
from lark import Token, Tree, Visitor, v_args
from lark.exceptions import VisitError

from tracecat.expressions import functions
from tracecat.expressions.common import VISITOR_NODE_TO_EXPR_TYPE, ExprContext, ExprType
from tracecat.logger import logger
from tracecat.secrets.constants import DEFAULT_SECRETS_ENVIRONMENT
from tracecat.validation.models import ExprValidationResult, ValidationDetail


class BaseExprValidator(Visitor):
    """Base validator containing common validation logic.

    You should not use this class directly, but rather use one of the subclasses.
    """

    _visitor_name: str
    _expr_kind: str

    def __init__(
        self,
        validators: dict[ExprType, Callable[..., Awaitable[ValidationDetail]]]
        | None = None,
        *,
        environment: str = DEFAULT_SECRETS_ENVIRONMENT,
        strict: bool = True,
    ) -> None:
        self._results: list[ExprValidationResult] = []
        self._strict = strict
        self._loc: tuple[str | int, ...] = ("expression",)
        self._environment = environment
        self._validators = validators or {}
        self.logger = logger.bind(visitor=self._visitor_name)

    """Utility"""

    def add(
        self,
        status: Literal["success", "error"],
        msg: str = "",
        type: ExprType = ExprType.GENERIC,
        ref: str | None = None,
        loc: tuple[str | int, ...] | None = None,
    ) -> None:
        self._results.append(
            ExprValidationResult(
                status=status,
                msg=msg,
                expression_type=type,
                ref=ref,
                expression=".".join(map(str, loc or self._loc)),
            )
        )

    def results(self) -> Iterator[ExprValidationResult]:
        """Return all validation results."""
        yield from self._results

    def errors(self) -> list[ExprValidationResult]:
        """Return all validation errors."""
        return [res for res in self.results() if res.status == "error"]

    def visit_with_locator(
        self,
        tree: Tree,
        loc: tuple[str | int, ...] | None = None,
        exclude: set[ExprType] | None = None,
    ) -> Any:
        self._loc = loc or self._loc
        self._exclude = exclude or set()
        return self.visit(tree)

    @override
    def visit(self, tree: Tree) -> Any:
        try:
            if VISITOR_NODE_TO_EXPR_TYPE.get(tree.data) in self._exclude:
                logger.trace("Skipping node", node=tree.data)
                return
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

    def actions(self, node: Tree[Token]):
        self.add(
            status="error",
            type=ExprType.ACTION,
            msg=f"ACTIONS expressions are not supported in {self._expr_kind}",
        )

    def inputs(self, node: Tree[Token]):
        self.add(
            status="error",
            type=ExprType.INPUT,
            msg=f"INPUTS expressions are not supported in {self._expr_kind}",
        )

    def trigger(self, node: Tree):
        self.add(
            status="error",
            type=ExprType.TRIGGER,
            msg=f"TRIGGER expressions are not supported in {self._expr_kind}",
        )

    def env(self, node: Tree):
        self.add(
            status="error",
            type=ExprType.ENV,
            msg=f"ENV expressions are not supported in {self._expr_kind}",
        )

    def local_vars(self, node: Tree):
        self.add(
            status="error",
            type=ExprType.LOCAL_VARS,
            msg=f"var expressions are not supported in {self._expr_kind}",
        )

    def iterator(self, node: Tree):
        self.logger.trace("Visit iterator expression", node=node)
        self.add(
            status="error",
            type=ExprType.ITERATOR,
            msg=f"for_each expressions are not supported in {self._expr_kind}",
        )

    def secrets(self, node: Tree[Token]) -> tuple[str, str] | None:
        self.logger.trace("Visit secret expression", expr=node)

        expr = node.children[0]
        if not isinstance(expr, Token):
            raise ValueError("Expected a string token")
        try:
            sec_path = expr.lstrip(".")
        except ValueError:
            sec_jsonpath = ExprContext.SECRETS + expr
            return self.add(
                status="error",
                msg=f"Invalid secret usage: {sec_jsonpath!r}. Must be in the format `SECRETS.my_secret.KEY`",
                type=ExprType.SECRET,
            )
        parts = sec_path.split(".")
        if len(parts) > 2:
            return self.add(
                status="error",
                msg=f"Invalid secret usage: {sec_path!r}. Got extra segments {parts[2:]!r}."
                "Must be in the format `SECRETS.my_secret.KEY`",
                type=ExprType.SECRET,
            )
        elif len(parts) == 1:
            return self.add(
                status="error",
                msg=f"Invalid secret usage: {sec_path!r}. Must be in the format `SECRETS.my_secret.KEY`",
                type=ExprType.SECRET,
            )
        name, key = parts
        return name, key

    def function(self, node: Tree[Token]):
        fn_name = node.children[0]
        if not isinstance(fn_name, Token):
            raise ValueError("Expected a string token")
        is_mapped = fn_name.endswith(".map")
        fn_name = fn_name.rsplit(".", 1)[0] if is_mapped else fn_name
        self.logger.trace(
            "Visit function expression",
            fn_name=node,
            is_mapped=is_mapped,
            node=node,
        )

        # 1. Verify that the function exists in FUNCTION_MAPPING
        func = functions.FUNCTION_MAPPING.get(fn_name)
        if func is None:
            self.add(
                status="error",
                msg=f"Unknown function name {str(fn_name)!r}",
                type=ExprType.FUNCTION,
            )
            return

        # 2. Validate argument count (positional only – keyword args are not supported
        #    in template expressions).
        #    We deliberately keep the logic simple:
        #    * Ensure the number of provided arguments is **at least** the number of
        #      required positional parameters.
        #    * If the function does **not** declare *args, ensure we don't pass more
        #      arguments than positional parameters (including those with defaults).

        # Extract argument list from the parse tree (may be missing for zero-arg calls)
        arg_list_node = node.children[1] if len(node.children) > 1 else None
        provided_arg_count = 0
        if isinstance(arg_list_node, Tree) and arg_list_node.data == "arg_list":
            # Children of arg_list include both the argument expressions and literal comma
            # tokens that separate them (","), e.g. [expr1, ',', expr2]. We need to
            # consider only the actual expressions.
            provided_arg_count = sum(
                1
                for child in arg_list_node.children
                # Exclude separator tokens injected by the grammar
                if not (isinstance(child, Token) and child.value == ",")
            )

        # Retrieve the original wrapped function (mappable decorator keeps original in
        # __wrapped__). Fallback to the function itself if __wrapped__ is absent.

        original_func = getattr(func, "__wrapped__", func)
        sig = inspect.signature(original_func)

        required_positional = [
            p
            for p in sig.parameters.values()
            if p.kind
            in (
                inspect.Parameter.POSITIONAL_ONLY,
                inspect.Parameter.POSITIONAL_OR_KEYWORD,
            )
            and p.default is inspect.Parameter.empty
        ]

        max_positional = [
            p
            for p in sig.parameters.values()
            if p.kind
            in (
                inspect.Parameter.POSITIONAL_ONLY,
                inspect.Parameter.POSITIONAL_OR_KEYWORD,
            )
        ]

        # Determine if the function accepts *args
        has_varargs = any(
            p.kind == inspect.Parameter.VAR_POSITIONAL for p in sig.parameters.values()
        )

        # Validate minimum argument count
        if provided_arg_count < len(required_positional):
            self.add(
                status="error",
                msg=(
                    f"Function {fn_name!r} expects at least {len(required_positional)} "
                    f"argument(s) but {provided_arg_count} were provided"
                ),
                type=ExprType.FUNCTION,
            )
            return

        # Validate maximum argument count only if *args not present
        if not has_varargs and provided_arg_count > len(max_positional):
            self.add(
                status="error",
                msg=(
                    f"Function {fn_name!r} accepts at most {len(max_positional)} "
                    f"argument(s) but {provided_arg_count} were provided"
                ),
                type=ExprType.FUNCTION,
            )
            return

        # If we reach here, the function reference and argument count are valid
        self.add(status="success", type=ExprType.FUNCTION)

    def ternary(self, node: Tree):
        cond_expr, true_expr, false_expr = node.children
        self.logger.trace(
            "Visit ternary expression",
            cond_expr=cond_expr,
            true_expr=true_expr,
            false_expr=false_expr,
        )
        self.add(status="success", type=ExprType.TERNARY)

    def typecast(self, node: Tree[Token]):
        self.logger.trace("Visit typecast expression")
        typename, *children = node.children
        if typename not in functions.BUILTIN_TYPE_MAPPING:
            return self.add(
                status="error",
                msg=f"Invalid type {typename!r}."
                f" Valid types are {list(functions.BUILTIN_TYPE_MAPPING.keys())}",
                type=ExprType.TYPECAST,
            )

        # If the child is a literal, we can typecast it directly
        child = children[0]
        if not isinstance(child, Tree):
            raise ValueError("Expected a tree")
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

    def jsonpath_expression(self, node: Tree[Token]):
        self.logger.trace("Visiting jsonpath expression", children=node.children)
        try:
            combined_segments = "".join(node.children)  # type: ignore
        except (AttributeError, ValueError) as e:
            self.logger.error("Invalid jsonpath segments", error=str(e))
            self.add(
                status="error",
                msg="Couldn't combine jsonpath expression segments: " + str(e),
            )
        try:
            jsonpath_ng.ext.parse("$" + combined_segments)
        except jsonpath_ng.exceptions.JSONPathError as e:
            self.logger.error("Invalid jsonpath body", error=str(e))
            self.add(
                status="error",
                msg=str(e),
            )

    @v_args(inline=True)
    def jsonpath_segment(self, *args):
        self.logger.trace("Visiting jsonpath segment", args=args)
