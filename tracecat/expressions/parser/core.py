from lark import Lark, Token, Tree
from lark.exceptions import UnexpectedCharacters, UnexpectedEOF, UnexpectedInput

from tracecat.exceptions import TracecatExpressionError
from tracecat.expressions.parser.grammar import grammar
from tracecat.logger import logger


class ExprParser:
    def __init__(self, start_rule: str = "root") -> None:
        self.parser = Lark(grammar, start=start_rule, parser="lalr")

    def parse(self, expression: str) -> Tree[Token] | None:
        try:
            return self.parser.parse(expression)
        except (UnexpectedCharacters, UnexpectedEOF, UnexpectedInput) as e:
            # The diagnostic quotes the source, which can embed literal
            # credentials; callers decide whether the raised error is safe.
            logger.error("Failed to parse expression", kind=e.__class__.__name__)
            if hasattr(e, "allowed"):
                # Zero out the allowed attribute to hide allowed characters
                e.allowed = None  # pyright: ignore[reportAttributeAccessIssue]
            raise TracecatExpressionError(
                f"Failed to parse expression: {e}", detail=str(e)
            ) from e
        except Exception as e:
            logger.error("Unexpected error parsing expression", kind=type(e).__name__)
            raise TracecatExpressionError(
                f"Unexpected error when parsing expression: {e!r}"
            ) from e


parser = ExprParser()
