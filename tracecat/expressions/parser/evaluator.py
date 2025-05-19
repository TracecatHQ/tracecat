from collections.abc import Sequence
from typing import Any, TypeVar, cast

from lark import Token, Transformer, Tree, v_args
from lark.exceptions import VisitError

from tracecat.expressions import functions
from tracecat.expressions.common import (
    ExprContext,
    ExprOperand,
    IterableExpr,
    eval_jsonpath,
)
from tracecat.logger import logger
from tracecat.types.exceptions import TracecatExpressionError

LiteralT = TypeVar("LiteralT", int, float, str, bool)


class ExprEvaluator(Transformer):
    _visitor_name: str = "ExprEvaluator"

    def __init__(
        self, operand: ExprOperand | None = None, strict: bool = False
    ) -> None:
        self._operand = cast(ExprOperand, operand or {})
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
    def root(self, node: Tree):
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
    def inputs(self, jsonpath: str):
        logger.trace("Visiting inputs:", args=jsonpath)
        expr = ExprContext.INPUTS + jsonpath
        return eval_jsonpath(expr, self._operand, strict=self._strict)

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
    def function(self, fn_name: str, fn_args: Sequence[Any]):
        is_mapped = fn_name.endswith(".map")
        fn_name = fn_name.rsplit(".", 1)[0] if is_mapped else fn_name
        self.logger.trace(
            "Visit function expression",
            fn_name=fn_name,
            fn_args=fn_args,
            is_mapped=is_mapped,
        )
        fn = functions.FUNCTION_MAPPING.get(fn_name)
        if fn is None:
            raise TracecatExpressionError(f"Unknown function {fn_name!r}")
        final_fn = fn.map if is_mapped else fn
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
