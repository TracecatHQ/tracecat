grammar = r"""
?root: expression
        | trailing_typecast_expression
        | iterator

trailing_typecast_expression: expression "->" TYPE_SPECIFIER
iterator: "for" local_vars_assignment "in" expression

?expression: context
          | literal
          | TYPE_SPECIFIER "(" expression ")" -> typecast
          | ternary
          | binary_op
          | list
          | dict
          | "(" expression ")"

ternary: expression "if" expression "else" expression
binary_op: expression OPERATOR expression

?context: actions
        | secrets
        | inputs
        | env
        | local_vars
        | trigger
        | function
        | template_action_inputs
        | template_action_steps

arg_list: (expression ("," expression)*)?


actions: "ACTIONS" PARTIAL_JSONPATH_EXPR
secrets: "SECRETS" ATTRIBUTE_PATH
inputs: "INPUTS" PARTIAL_JSONPATH_EXPR
env: "ENV" PARTIAL_JSONPATH_EXPR
local_vars: "var" PARTIAL_JSONPATH_EXPR
trigger: "TRIGGER" [PARTIAL_JSONPATH_EXPR]
function: "FN." FN_NAME_WITH_TRANSFORM "(" [arg_list] ")"
local_vars_assignment: "var" ATTRIBUTE_PATH

template_action_inputs: "inputs" PARTIAL_JSONPATH_EXPR
template_action_steps: "steps" PARTIAL_JSONPATH_EXPR



literal: STRING_LITERAL
        | BOOL_LITERAL
        | NUMERIC_LITERAL
        | NONE_LITERAL


list: "[" [arg_list] "]"
dict : "{" [kvpair ("," kvpair)*] "}"
kvpair : STRING_LITERAL ":" expression

ATTRIBUTE_PATH: ("." CNAME)+
FN_NAME_WITH_TRANSFORM: CNAME FN_TRANSFORM?
FN_TRANSFORM: "." CNAME

PARTIAL_JSONPATH_EXPR: /(?:\.(\.)?(?:[a-zA-Z_][a-zA-Z0-9_]*|\*|'[^']*'|"[^"]*"|\[[^\]]+\]|\`[^\`]*\`)|\.\.|\[[^\]]+\])+/

OPERATOR: "+" | "-" | "*" | "/" | "%" | "==" | "!=" | ">" | "<" | ">=" | "<=" | "&&" | "||" | "in"
TYPE_SPECIFIER: "int" | "float" | "str" | "bool"
STRING_LITERAL: /'(?:[^'\\]|\\.)*'/ | /"(?:[^"\\]|\\.)*"/
BOOL_LITERAL: "True" | "False"
NUMERIC_LITERAL: /\d+(\.\d+)?/
NONE_LITERAL: "None"



%import common.CNAME
%import common.INT -> NUMBER
%import common.WS
%ignore WS
"""
