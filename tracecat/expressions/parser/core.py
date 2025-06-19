import re
from lark import Lark, Token, Tree
from lark.exceptions import UnexpectedCharacters, UnexpectedEOF, UnexpectedInput

from tracecat.expressions.parser.grammar import grammar
from tracecat.logger import logger
from tracecat.types.exceptions import TracecatExpressionError


def _clean_lark_error_message(error_msg: str) -> tuple[str, int, int]:
    """
    Clean up Lark error messages to make them more user-friendly.
    
    Returns:
        tuple: (cleaned_message, line, column)
    """
    line = 1
    column = 1
    
    # Try to extract position information from error message
    try:
        pos_match = re.search(r"at line (\d+), column (\d+)", error_msg)
        if pos_match:
            line = int(pos_match.group(1))
            column = int(pos_match.group(2))
    except Exception:
        # Fallback to default position
        pass
    
    # Clean up error message for user display
    if "No terminal matches" in error_msg:
        # Extract the problematic character from the error message
        char_match = re.search(r"No terminal matches '([^']*)'", error_msg)
        if char_match:
            char = char_match.group(1)
            # Handle HTML entities
            if char.startswith("&") and char.endswith(";"):
                # Convert common HTML entities back to characters
                entity_map = {
                    "&lt;": "<",
                    "&gt;": ">", 
                    "&amp;": "&",
                    "&quot;": '"',
                    "&#x27;": "'",
                    "&#39;": "'",
                }
                char = entity_map.get(char, char)
            
            if char:
                clean_msg = f"Unexpected character '{char}' in expression"
            else:
                clean_msg = "Invalid character in expression"
        else:
            clean_msg = "Invalid character in expression"
    elif "Unexpected token" in error_msg:
        clean_msg = "Unexpected token in expression"
    elif "Expected" in error_msg:
        clean_msg = "Invalid syntax in expression"
    elif "Unexpected EOF" in error_msg or "UnexpectedEOF" in error_msg:
        clean_msg = "Expression is incomplete - missing closing brackets or quotes"
    else:
        # Fallback to a generic message for other parsing errors
        clean_msg = "Invalid expression syntax"
    
    return clean_msg, line, column


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
            
            # Clean up the error message for better user experience
            cleaned_msg, line, column = _clean_lark_error_message(str(e))
            
            # Create a more user-friendly error message
            if line > 1 or column > 1:
                user_msg = f"{cleaned_msg} at line {line}, column {column}"
            else:
                user_msg = cleaned_msg
                
            raise TracecatExpressionError(
                user_msg, 
                detail={
                    "original_error": str(e),
                    "line": line,
                    "column": column,
                    "expression": expression
                }
            ) from e
        except Exception as e:
            logger.error(e)
            raise TracecatExpressionError(
                "Unexpected error when parsing expression - please check your syntax",
                detail={"original_error": str(e), "expression": expression}
            ) from e


parser = ExprParser()
