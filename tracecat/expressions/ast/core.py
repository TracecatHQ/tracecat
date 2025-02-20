import sys
from collections.abc import Sequence
from dataclasses import dataclass
from typing import Any

from lark import Lark, Token, Transformer, ast_utils
from lark.tree import Meta

from tracecat.expressions.parser.evaluator import functions
from tracecat.expressions.parser.grammar import grammar
from tracecat.logger import logger

this_module = sys.modules[__name__]


#
#   Define AST
#
class _Ast(ast_utils.Ast):
    # This will be skipped by create_transformer(), because it starts with an underscore
    pass


class _Expression(_Ast):
    # This will be skipped by create_transformer(), because it starts with an underscore
    pass


@dataclass
class Value(_Ast, ast_utils.WithMeta):
    "Uses WithMeta to include line-number metadata in the meta attribute"

    meta: Meta
    value: object


@dataclass
class Name(_Ast):
    name: str


@dataclass
class CodeBlock(_Ast, ast_utils.AsList):
    # Corresponds to code_block in the grammar
    statements: list[_Expression]


@dataclass
class If(_Expression):
    cond: Value
    then: CodeBlock


@dataclass
class SetVar(_Expression):
    # Corresponds to set_var in the grammar
    name: str
    value: Value


@dataclass
class Print(_Expression):
    value: Value


@dataclass
class TrailingTypecastExpression(_Expression):
    value: Any
    typename: str


@dataclass
class Expression(_Expression):
    value: Any


@dataclass
class Context(_Expression):
    value: Any


@dataclass
class Iterator(_Expression):
    iter_var_expr: str
    collection: Any


@dataclass
class Typecast(_Expression):
    typename: str
    value: Any


@dataclass
class Ternary(_Expression):
    true_value: Any
    condition: bool
    false_value: Any


@dataclass
class List(_Expression, ast_utils.AsList):
    items: list[Any]


@dataclass
class Dict(_Expression):
    pairs: list[tuple[Any, Any]]


@dataclass
class KVPair(_Ast):
    key: Any
    value: Any


@dataclass
class Actions(_Expression):
    jsonpath: str


@dataclass
class Secrets(_Expression):
    path: str


@dataclass
class Inputs(_Expression):
    jsonpath: str


@dataclass
class Env(_Expression):
    jsonpath: str


@dataclass
class Lookup(_Expression):
    table_name: str
    method_name: str
    jsonpath: str | None


@dataclass
class LocalVars(_Expression):
    jsonpath: str


@dataclass
class LocalVarsAssignment(_Expression):
    jsonpath: str


@dataclass
class Trigger(_Expression):
    jsonpath: str | None


@dataclass
class TemplateActionInputs(_Expression):
    jsonpath: str


@dataclass
class TemplateActionSteps(_Expression):
    jsonpath: str


@dataclass
class Function(_Expression):
    fn_name: str
    fn_args: Sequence[Any]


@dataclass
class ArgList(_Ast, ast_utils.AsList):
    args: list[Any]


@dataclass
class Literal[T](_Expression):
    value: T


@dataclass
class BinaryOp(_Expression):
    lhs: Any
    op: str
    rhs: Any


class AstTransformer(Transformer):
    # Define extra transformation functions, for rules that don't correspond to an AST class.

    def PARTIAL_JSONPATH_EXPR(self, token: Token):
        logger.trace("Visiting PARTIAL_JSONPATH_EXPR:", value=token.value)
        return token.value

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


#
#   Define Parser
#

parser = Lark(grammar, start="root", parser="lalr")

transformer = ast_utils.create_transformer(this_module, AstTransformer())


def parse(text):
    tree = parser.parse(text)
    return transformer.transform(tree)


#
#   Test
#

if __name__ == "__main__":
    print(
        parse("""
        ACTIONS.path.result
    """)
    )
