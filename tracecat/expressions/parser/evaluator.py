from collections.abc import Sequence
from typing import Any, TypeVar

from lark import Token, Transformer, Tree, v_args
from lark.exceptions import VisitError

from tracecat.expressions import functions
from tracecat.expressions.shared import ExprContext, ExprContextType, IterableExpr
from tracecat.logging import logger
from tracecat.types.exceptions import TracecatExpressionError

LiteralT = TypeVar("LiteralT", int, float, str, bool)
T = TypeVar("T")


class TracecatTransformer(Transformer):
    def evaluate(self, tree: Tree):
        try:
            return self.transform(tree)
        except VisitError as e:
            logger.error(e)
            raise TracecatExpressionError(
                f"Failed to evaluate expression: {e}", detail=str(e)
            ) from e


class ExprEvaluator(TracecatTransformer):
    _visitor_name = "ExprEvaluator"

    def __init__(self, context: ExprContextType, strict: bool = False) -> None:
        self._context = context
        self._strict = strict
        self.logger = logger.bind(visitor=self._visitor_name)

    @v_args(inline=True)
    def root(self, node: Tree):
        logger.trace("Visiting root:", node=node)
        return node

    @v_args(inline=True)
    def trailing_typecast_expression(self, value: T, typename: str):
        logger.trace(
            "Visiting trailing_typecast_expression:", value=value, typename=typename
        )
        return functions.cast(value, typename)

    @v_args(inline=True)
    def expression(self, value: T):
        logger.trace("Visiting expression:", args=value)
        return value

    @v_args(inline=True)
    def context(self, value: T):
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
        return functions.eval_jsonpath(
            ExprContext.ACTIONS + jsonpath, self._context, strict=self._strict
        )

    @v_args(inline=True)
    def secrets(self, jsonpath: str):
        logger.trace("Visiting secrets:", jsonpath=jsonpath)
        return functions.eval_jsonpath(
            ExprContext.SECRETS + jsonpath, self._context, strict=self._strict
        )

    @v_args(inline=True)
    def inputs(self, jsonpath: str):
        logger.trace("Visiting inputs:", args=jsonpath)
        return functions.eval_jsonpath(
            ExprContext.INPUTS + jsonpath, self._context, strict=self._strict
        )

    @v_args(inline=True)
    def env(self, jsonpath: str):
        logger.trace("Visiting env:", args=jsonpath)
        return functions.eval_jsonpath(
            ExprContext.ENV + jsonpath, self._context, strict=self._strict
        )

    @v_args(inline=True)
    def local_vars(self, jsonpath: str):
        logger.trace("Visiting local_vars:", args=jsonpath)
        return functions.eval_jsonpath(
            ExprContext.LOCAL_VARS + jsonpath, self._context, strict=self._strict
        )

    @v_args(inline=True)
    def local_vars_assignment(self, jsonpath: str):
        logger.trace("Visiting local_vars_assignment:", args=jsonpath)
        return jsonpath

    @v_args(inline=True)
    def trigger(self, jsonpath: str):
        logger.trace("Visiting trigger:", args=jsonpath)
        return functions.eval_jsonpath(
            ExprContext.TRIGGER + jsonpath, self._context, strict=self._strict
        )

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
            raise TracecatExpressionError(
                f"Unknown function {fn_name!r}." f" ({is_mapped=})"
            )
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

    @v_args(inline=True)
    def jsonpath_expression(self, *args):
        logger.trace("Visiting jsonpath expression", args=args)
        return "".join(args)

    @v_args(inline=True)
    def jsonpath_segment(self, *args):
        logger.trace("Visiting jsonpath segment", args=args)
        return "".join(args)

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
