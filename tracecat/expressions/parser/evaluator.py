from collections.abc import Mapping, Sequence
from typing import Any, TypeVar

from lark import Token, Transformer, Tree, v_args
from lark.exceptions import VisitError

from tracecat.exceptions import TracecatExpressionError
from tracecat.expressions import functions
from tracecat.expressions.common import (
    MAX_VARS_PATH_DEPTH,
    ExprContext,
    ExprOperand,
    IterableExpr,
    eval_jsonpath,
)
from tracecat.logger import logger

LiteralT = TypeVar("LiteralT", int, float, str, bool)


class ExprEvaluator(Transformer[Token, Any]):
    _visitor_name: str = "ExprEvaluator"

    def __init__(
        self, operand: ExprOperand[str] | None = None, strict: bool = False
    ) -> None:
        super().__init__()
        self._operand: ExprOperand[str] = operand or {}
        self._strict = strict
        self.logger = logger.bind(visitor=self._visitor_name)

    def evaluate(self, tree: Tree[Token]) -> Any:
        try:
            return self.transform(tree)
        except VisitError as e:
            logger.error(
                "Evaluation failed at node",
                node=e.obj,
                reason=e.orig_exc,
            )
            raise TracecatExpressionError(
                f"[evaluator] Evaluation failed at node:\n```\n{tree.pretty()}\n```\nReason: {e}",
                detail=str(e),
            ) from e

    @v_args(inline=True)
    def root(self, node: Tree[Token]) -> Tree[Token]:
        logger.trace("Visiting root:", node=node)
        return node

    @v_args(inline=True)
    def trailing_typecast_expression(self, value: Any, typename: str):
        logger.trace(
            "Visiting trailing_typecast_expression:", value=value, typename=typename
        )
        return functions.cast(value, typename)

    @v_args(inline=True)
    def expression(self, value: Any):
        logger.trace("Visiting expression:", args=value)
        return value

    @v_args(inline=True)
    def context(self, value: Any):
        logger.trace("Visiting context:", args=value)
        return value

    @v_args(inline=True)
    def base_expr(self, value: Any):
        logger.trace("Visiting base_expr:", value=value)
        return value

    @v_args(inline=True)
    def indexer(self, index: Any):
        logger.trace("Visiting indexer:", index=index)
        return index

    @v_args(inline=True)
    def primary_expr(self, base: Any, *indexers: Any):
        logger.trace(
            "Visiting primary_expr:",
            base=base,
            indexers=indexers,
        )
        result = base
        for index in indexers:
            result = self._apply_index(result, index)
        return result

    @v_args(inline=True)
    def iterator(self, iter_var_expr: str, collection: Any):
        self.logger.trace(
            "Visit iterator expression",
            iter_var_expr=iter_var_expr,
            collection=collection,
        )
        # Ensure that our collection is an iterable
        # We have to evaluate the collection expression
        if not hasattr(collection, "__iter__"):
            raise ValueError(
                f"Invalid iterator collection: {collection!r}. Must be an iterable."
            )

        # Reset the loop flag
        return IterableExpr(iter_var_expr, collection)

    @v_args(inline=True)
    def typecast(self, typename: str, value: Any):
        logger.trace("Visiting typecast:", args=value)
        return functions.cast(value, typename)

    @v_args(inline=True)
    def ternary(self, true_value: Any, condition: bool, false_value: Any):
        logger.trace("Visiting ternary:", true_value=true_value, condition=condition)
        return true_value if condition else false_value

    @v_args(inline=True)
    def list(self, *args):
        logger.trace("Visiting list:", args=args)
        return list(*args)

    @v_args(inline=True)
    def dict(self, *args):
        logger.trace("Visiting dict:", args=args)
        return dict(args)

    @v_args(inline=True)
    def kvpair(self, *args):
        logger.trace("Visiting kvpair:", args=args)
        return args

    @v_args(inline=True)
    def actions(self, jsonpath: str):
        logger.trace("Visiting actions:", args=jsonpath)
        expr = ExprContext.ACTIONS + jsonpath
        return eval_jsonpath(expr, self._operand, strict=self._strict)

    @v_args(inline=True)
    def secrets(self, path: str):
        logger.trace("Visiting secrets:", path=path)
        expr = ExprContext.SECRETS + path
        return eval_jsonpath(expr, self._operand or {}, strict=self._strict)

    @v_args(inline=True)
    def vars(self, path: str):
        logger.trace("Visiting vars:", path=path)
        trimmed = path.lstrip(".")
        parts = trimmed.split(".") if trimmed else []
        key_segments = parts[1:] if len(parts) > 1 else []
        if len(key_segments) > MAX_VARS_PATH_DEPTH:
            formatted = ".".join(parts)
            raise TracecatExpressionError(
                "VARS expressions currently support at most one key segment "
                "(`VARS.<name>.<key>`). "
                f"Got VARS.{formatted!s} with {len(key_segments)} key segments after the variable name."
            )
        expr = ExprContext.VARS + path
        return eval_jsonpath(expr, self._operand or {}, strict=self._strict)

    @v_args(inline=True)
    def env(self, jsonpath: str):
        logger.trace("Visiting env:", args=jsonpath)
        expr = ExprContext.ENV + jsonpath
        return eval_jsonpath(expr, self._operand, strict=self._strict)

    @v_args(inline=True)
    def local_vars(self, jsonpath: str):
        logger.trace("Visiting local_vars:", args=jsonpath)
        expr = ExprContext.LOCAL_VARS + jsonpath
        return eval_jsonpath(expr, self._operand, strict=self._strict)

    @v_args(inline=True)
    def local_vars_assignment(self, jsonpath: str):
        logger.trace("Visiting local_vars_assignment:", args=jsonpath)
        return jsonpath

    @v_args(inline=True)
    def trigger(self, jsonpath: str | None):
        logger.trace("Visiting trigger:", args=jsonpath)
        expr = ExprContext.TRIGGER + (jsonpath or "")
        return eval_jsonpath(expr, self._operand, strict=self._strict)

    @v_args(inline=True)
    def template_action_inputs(self, jsonpath: str):
        logger.trace("Visiting template_action_inputs:", args=jsonpath)
        expr = ExprContext.TEMPLATE_ACTION_INPUTS + jsonpath
        return eval_jsonpath(expr, self._operand, strict=self._strict)

    @v_args(inline=True)
    def template_action_steps(self, jsonpath: str):
        logger.trace("Visiting template_action_steps:", args=jsonpath)
        expr = ExprContext.TEMPLATE_ACTION_STEPS + jsonpath
        return eval_jsonpath(expr, self._operand, strict=self._strict)

    @v_args(inline=True)
    def function(self, fn_name: str, fn_args: Sequence[Any] | None):
        is_mapped = fn_name.endswith(".map")
        fn_name = fn_name.rsplit(".", 1)[0] if is_mapped else fn_name
        # Handle None args (empty function calls like FN.now())
        if fn_args is None:
            fn_args = ()
        self.logger.trace(
            "Visit function expression",
            fn_name=fn_name,
            fn_args=fn_args,
            is_mapped=is_mapped,
        )
        fn = functions.FUNCTION_MAPPING.get(fn_name)
        if fn is None:
            raise TracecatExpressionError(f"Unknown function {fn_name!r}")
        final_fn = fn.map if is_mapped else fn  # pyright: ignore[reportFunctionMemberAccess] # ty: ignore[possibly-missing-attribute]
        result = final_fn(*fn_args)
        self.logger.trace(f"Function {fn_name!r} returned {result!r}")
        return result

    @v_args(inline=True)
    def arg_list(self, *args):
        logger.trace("Visiting arg_list:", args=args)
        return args

    @v_args(inline=True)
    def literal(self, value: LiteralT) -> LiteralT:
        logger.trace("Visiting literal:", value=value)
        return value

    @v_args(inline=True)
    def binary_op(self, lhs: Any, op: str, rhs: Any):
        logger.trace("Visiting binary_op:", lhs=lhs, op=op, rhs=rhs)
        return functions.OPERATORS[op](lhs, rhs)

    # Logical operators
    @v_args(inline=True)
    def or_op(self, lhs: Any, rhs: Any):
        logger.trace("Visiting or_op:", lhs=lhs, rhs=rhs)
        return functions.OPERATORS["||"](lhs, rhs)

    @v_args(inline=True)
    def and_op(self, lhs: Any, rhs: Any):
        logger.trace("Visiting and_op:", lhs=lhs, rhs=rhs)
        return functions.OPERATORS["&&"](lhs, rhs)

    @v_args(inline=True)
    def not_op(self, value: Any):
        logger.trace("Visiting not_op:", value=value)
        return not value

    # Comparison operators
    @v_args(inline=True)
    def eq_op(self, lhs: Any, rhs: Any):
        logger.trace("Visiting eq_op:", lhs=lhs, rhs=rhs)
        return functions.OPERATORS["=="](lhs, rhs)

    @v_args(inline=True)
    def ne_op(self, lhs: Any, rhs: Any):
        logger.trace("Visiting ne_op:", lhs=lhs, rhs=rhs)
        return functions.OPERATORS["!="](lhs, rhs)

    @v_args(inline=True)
    def gt_op(self, lhs: Any, rhs: Any):
        logger.trace("Visiting gt_op:", lhs=lhs, rhs=rhs)
        return functions.OPERATORS[">"](lhs, rhs)

    @v_args(inline=True)
    def ge_op(self, lhs: Any, rhs: Any):
        logger.trace("Visiting ge_op:", lhs=lhs, rhs=rhs)
        return functions.OPERATORS[">="](lhs, rhs)

    @v_args(inline=True)
    def lt_op(self, lhs: Any, rhs: Any):
        logger.trace("Visiting lt_op:", lhs=lhs, rhs=rhs)
        return functions.OPERATORS["<"](lhs, rhs)

    @v_args(inline=True)
    def le_op(self, lhs: Any, rhs: Any):
        logger.trace("Visiting le_op:", lhs=lhs, rhs=rhs)
        return functions.OPERATORS["<="](lhs, rhs)

    # Inclusion operators
    @v_args(inline=True)
    def in_op(self, lhs: Any, rhs: Any):
        logger.trace("Visiting in_op:", lhs=lhs, rhs=rhs)
        return functions.OPERATORS["in"](lhs, rhs)

    @v_args(inline=True)
    def not_in_op(self, lhs: Any, rhs: Any):
        logger.trace("Visiting not_in_op:", lhs=lhs, rhs=rhs)
        return functions.OPERATORS["not in"](lhs, rhs)

    # Identity operators
    @v_args(inline=True)
    def is_op(self, lhs: Any, rhs: Any):
        logger.trace("Visiting is_op:", lhs=lhs, rhs=rhs)
        return functions.OPERATORS["is"](lhs, rhs)

    @v_args(inline=True)
    def is_not_op(self, lhs: Any, rhs: Any):
        logger.trace("Visiting is_not_op:", lhs=lhs, rhs=rhs)
        return functions.OPERATORS["is not"](lhs, rhs)

    # Arithmetic operators
    @v_args(inline=True)
    def add_op(self, lhs: Any, rhs: Any):
        logger.trace("Visiting add_op:", lhs=lhs, rhs=rhs)
        return functions.OPERATORS["+"](lhs, rhs)

    @v_args(inline=True)
    def sub_op(self, lhs: Any, rhs: Any):
        logger.trace("Visiting sub_op:", lhs=lhs, rhs=rhs)
        return functions.OPERATORS["-"](lhs, rhs)

    @v_args(inline=True)
    def mul_op(self, lhs: Any, rhs: Any):
        logger.trace("Visiting mul_op:", lhs=lhs, rhs=rhs)
        return functions.OPERATORS["*"](lhs, rhs)

    @v_args(inline=True)
    def div_op(self, lhs: Any, rhs: Any):
        logger.trace("Visiting div_op:", lhs=lhs, rhs=rhs)
        return functions.OPERATORS["/"](lhs, rhs)

    @v_args(inline=True)
    def mod_op(self, lhs: Any, rhs: Any):
        logger.trace("Visiting mod_op:", lhs=lhs, rhs=rhs)
        return functions.OPERATORS["%"](lhs, rhs)

    # Unary operators
    @v_args(inline=True)
    def neg_op(self, value: Any):
        logger.trace("Visiting neg_op:", value=value)
        return -value

    @v_args(inline=True)
    def pos_op(self, value: Any):
        logger.trace("Visiting pos_op:", value=value)
        return +value

    def PARTIAL_JSONPATH_EXPR(self, token: Token):
        logger.trace("Visiting PARTIAL_JSONPATH_EXPR:", value=token.value)
        return token.value

    """Terminals"""

    def JSONPATH(self, token: Token):
        logger.trace("Visiting jsonpath:", value=token.value)
        return token

    def JSONPATH_INDEX(self, token: Token):
        logger.trace("Visiting jsonpath_index:", value=token.value)
        return token.value

    def CNAME(self, token: Token):
        logger.trace("Visiting CNAME:", token=token, value=token.value)
        return token.value

    def OPERATOR(self, token: Token):
        logger.trace("Visiting OPERATOR:", value=token.value)
        return token.value

    def STRING_LITERAL(self, token: Token):
        logger.trace("Visiting STRING_LITERAL:", value=token.value)
        return token.value[1:-1]

    def NUMERIC_LITERAL(self, token: Token):
        logger.trace("Visiting NUMERIC_LITERAL:", value=token.value)
        if token.value.isdigit():
            return int(token.value)
        return float(token.value)

    def TYPE_SPECIFIER(self, token: Token):
        logger.trace("Visiting TYPE_SPECIFIER:", value=token.value)
        return token.value

    def BOOL_LITERAL(self, token: Token):
        logger.trace("Visiting BOOL_LITERAL:", value=token.value)
        return functions.cast(token.value, "bool")

    def NONE_LITERAL(self, token: Token):
        logger.trace("Visiting NONE_LITERAL:", value=token.value)
        return None

    def FN_NAME_WITH_TRANSFORM(self, token: Token):
        logger.trace("Visiting FN_NAME_WITH_TRANSFORM:", value=token.value)
        return token.value

    def ATTRIBUTE_PATH(self, token: Token):
        logger.trace("Visiting ATTRIBUTE_PATH:", value=token.value)
        return token.value

    def ATTRIBUTE_ACCESS(self, token: Token):
        logger.trace("Visiting ATTRIBUTE_ACCESS:", value=token.value)
        return token.value

    def BRACKET_ACCESS(self, token: Token):
        logger.trace("Visiting BRACKET_ACCESS:", value=token.value)
        return token.value

    def _apply_index(self, value: Any, index: Any) -> Any:
        self.logger.trace("Applying index", value=value, index=index)
        if isinstance(value, Mapping):
            try:
                return value[index]
            except KeyError as exc:
                raise TracecatExpressionError(
                    f"Key {index!r} not found for mapping access"
                ) from exc
            except TypeError as exc:
                raise TracecatExpressionError(
                    f"Invalid key type {type(index).__name__!r} for mapping access"
                ) from exc

        if isinstance(value, Sequence):
            if not isinstance(index, int):
                raise TracecatExpressionError(
                    f"Sequence indices must be integers, got {type(index).__name__!r}"
                )
            try:
                return value[index]
            except IndexError as exc:
                raise TracecatExpressionError(
                    f"Sequence index {index} out of range"
                ) from exc

        raise TracecatExpressionError(
            f"Object of type {type(value).__name__!r} is not indexable"
        )
