grammar = r"""
?root: expression
        | trailing_typecast_expression
        | iterator

trailing_typecast_expression: expression "->" TYPE_SPECIFIER
iterator: "for" local_vars_assignment "in" expression

// Expression hierarchy with proper precedence for LALR
?expression: ternary_expr

?ternary_expr: or_expr
             | or_expr "if" or_expr "else" ternary_expr -> ternary

?or_expr: and_expr
        | or_expr "||" and_expr -> or_op

?and_expr: not_expr
         | and_expr "&&" not_expr -> and_op

?not_expr: comparison_expr
         | "not" comparison_expr -> not_op

?comparison_expr: inclusion_expr
                | comparison_expr "==" inclusion_expr -> eq_op
                | comparison_expr "!=" inclusion_expr -> ne_op
                | comparison_expr ">" inclusion_expr -> gt_op
                | comparison_expr ">=" inclusion_expr -> ge_op
                | comparison_expr "<" inclusion_expr -> lt_op
                | comparison_expr "<=" inclusion_expr -> le_op

?inclusion_expr: identity_expr
               | inclusion_expr "in" identity_expr -> in_op
               | inclusion_expr "not" "in" identity_expr -> not_in_op

?identity_expr: addition_expr
              | identity_expr "is" addition_expr -> is_op
              | identity_expr "is" "not" addition_expr -> is_not_op

?addition_expr: multiplication_expr
              | addition_expr "+" multiplication_expr -> add_op
              | addition_expr "-" multiplication_expr -> sub_op

?multiplication_expr: unary_expr
                    | multiplication_expr "*" unary_expr -> mul_op
                    | multiplication_expr "/" unary_expr -> div_op
                    | multiplication_expr "%" unary_expr -> mod_op

?unary_expr: primary_expr
           | "-" unary_expr -> neg_op
           | "+" unary_expr -> pos_op

?primary_expr: atom
             | TYPE_SPECIFIER "(" expression ")" -> typecast
             | "(" expression ")"

?atom: context
     | literal
     | list
     | dict

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
