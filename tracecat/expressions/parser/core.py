from lark import Lark, Token, Tree
from lark.exceptions import UnexpectedCharacters, UnexpectedEOF, UnexpectedInput

from tracecat.expressions.parser.grammar import grammar
from tracecat.logger import logger
from tracecat.types.exceptions import TracecatExpressionError


class ExprParser:
    def __init__(self, start_rule: str = "root") -> None:
        self.parser = Lark(grammar, start=start_rule)

    def parse(self, expression: str) -> Tree[Token] | None:
        try:
            return self.parser.parse(expression)
        except (UnexpectedCharacters, UnexpectedEOF, UnexpectedInput) as e:
            logger.error(
                "Failed to parse expression",
                kind=e.__class__.__name__,
                detail=str(e),
            )
            if hasattr(e, "allowed"):
                # Zero out the allowed attribute to hide allowed characters
                e.allowed = None  # type: ignore
            raise TracecatExpressionError(
                f"Failed to parse expression: {e}", detail=str(e)
            ) from e
        except Exception as e:
            logger.error(e)
            raise TracecatExpressionError(
                f"Unexpected error when parsing expression: {e!r}"
            ) from e


parser = ExprParser()
