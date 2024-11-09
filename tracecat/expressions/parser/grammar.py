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
        | variables
        | env
        | local_vars
        | trigger
        | function
        | template_action_inputs
        | template_action_steps

arg_list: (expression ("," expression)*)?


actions: "ACTIONS" jsonpath_expression
secrets: "SECRETS" ATTRIBUTE_PATH
variables: "VARS" jsonpath_expression
env: "ENV" jsonpath_expression
local_vars: "var" jsonpath_expression
trigger: "TRIGGER" [jsonpath_expression]
function: "FN." FN_NAME_WITH_TRANSFORM "(" [arg_list] ")"
local_vars_assignment: "var" ATTRIBUTE_PATH

template_action_inputs: "inputs" jsonpath_expression
template_action_steps: "steps" jsonpath_expression



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

jsonpath_expression: jsonpath_segment+
?jsonpath_segment: ATTRIBUTE_ACCESS | BRACKET_ACCESS
JSONPATH_INDEX: /\d+/ | "*"
ATTRIBUTE_ACCESS: "." ( CNAME | STRING_LITERAL | BRACKET_ACCESS )
BRACKET_ACCESS: "[" (STRING_LITERAL | JSONPATH_INDEX) "]"

OPERATOR: "+" | "-" | "*" | "/" | "%" | "==" | "!=" | ">" | "<" | ">=" | "<=" | "&&" | "||" | "in"
TYPE_SPECIFIER: "int" | "float" | "str" | "bool"
STRING_LITERAL: /'(?:[^'\\]|\\.)*'/ | /"(?:[^"\\]|\\.)*"/
BOOL_LITERAL: "True" | "False"
NUMERIC_LITERAL: /\d+(\.\d+)?/
NONE_LITERAL: "None"



%import common.CNAME
%import common.WS
%ignore WS
"""
