from lark import Lark, LarkError, Tree

from tracecat.expressions.parser.grammar import grammar
from tracecat.logging import logger
from tracecat.types.exceptions import TracecatExpressionError


class ExprParser:
    def __init__(self, start_rule: str = "root") -> None:
        self.parser = Lark(grammar, start=start_rule)

    def parse(self, expression: str) -> Tree | None:
        try:
            return self.parser.parse(expression)
        except LarkError as e:
            logger.error(e)
            raise TracecatExpressionError(
                f"Failed to parse expression: {e}", detail=str(e)
            ) from e


parser = ExprParser()
