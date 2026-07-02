# Expressions in action args

Action `args` reference runtime data with template expressions: `${{ <namespace>.<path> }}`.
The `<path>` part is a **JSONPath** expression — dotted segments (`TRIGGER.alert.severity`),
bracket indexing (`ACTIONS.fetch.result.data[0]`), quoted keys for names with special characters
(`TRIGGER["alert-id"]`), and wildcards (`ACTIONS.fetch.result.items[*].name`) all work. The value
is resolved when the workflow runs. Three things commonly trip up authoring.

## 1. Namespaces are UPPERCASE

Every expression starts with one of these namespaces, written in capitals:

- `${{ TRIGGER.* }}` — the inbound trigger payload (webhook body, case event, manual input).
- `${{ ACTIONS.<ref>.result.* }}` — the output of an earlier action, by its `ref`.
- `${{ SECRETS.<name>.<key> }}` — a workspace secret.
- `${{ VARS.* }}` — a workspace variable.
- `${{ ENV.* }}` — execution environment metadata.
- `${{ FN.<function>(...) }}` — a built-in function call.

Lowercase (`${{ actions.x }}`, `${{ trigger.x }}`) is a parse error. When you see an
expression-parsing error, check the namespace case first.

## 2. The draft read-back shows `None`, not the expression

After you set an arg to `${{ TRIGGER.summary }}` and then call `get_workflow`, the value may come
back as `None`. This is expected and harmless:

- Expressions are evaluated against an **empty context at author time** — there is no live trigger
  or upstream action result yet, so the expression resolves to `None`.
- The expression is **stored correctly** and resolves to the real value **at runtime**.

So a `None` in the read-back is a display artifact, not a wiring bug. To confirm an action is wired
correctly, re-read its `args` (the expression string), not the resolved value.

## 3. Required string (and other typed) fields need a cast

Some action args are validated against a concrete type — e.g. a `case_id` that must be a string.
A bare `${{ TRIGGER.id }}` is **rejected at author time** with something like
"Input should be a valid string", because the expression resolves to `None` during validation and
`None` is not a string.

Fix it with the trailing typecast operator `-> <type>`:

```
case_id: ${{ TRIGGER.id -> str }}
```

- At author/validation time, `str(None) == "None"`, which passes the string check.
- At runtime, `str(<real id>)` yields the real value.

Supported cast types: `str`, `int`, `float`, `bool`, `datetime`. Use `-> str` for any expression
feeding a required string field; use the matching type for numeric/boolean/datetime fields.

## Full expression grammar

The exact grammar the platform parses inside `${{ ... }}` (Lark, LALR). Use it to check operator
support and precedence when composing anything beyond a plain path lookup:

```lark
?root: expression
     | trailing_typecast_expression        // <expr> -> str|int|float|bool
     | iterator                            // for var.x in <expr>

// Precedence, loosest to tightest:
//   ternary (a if cond else b)
//   ||   &&   not
//   ==  !=  >  >=  <  <=
//   in / not in
//   is / is not
//   +  -
//   *  /  %
//   unary -  +
//   indexing expr[expr], typecast int(expr), parens

?context: actions | secrets | vars | env | local_vars | trigger
        | function                          // FN.<name>(args...)

actions:  "ACTIONS" <jsonpath>              // e.g. ACTIONS.fetch.result.data[0]
secrets:  "SECRETS" .name.key
vars:     "VARS" .path
env:      "ENV" <jsonpath>
trigger:  "TRIGGER" [<jsonpath>]            // bare TRIGGER is the whole payload
local_vars: "var" <jsonpath>                // loop variable inside `for` iterators
function: "FN." name["." transform] "(" [expression ("," expression)*] ")"

literal: 'single' | "double" quoted strings
       | True | False | None
       | integers and floats

list: "[" expr ("," expr)* "]"
dict: "{" "key": expr ("," "key": expr)* "}"

// <jsonpath> segments: .name  .*  ."quoted name"  [index]  [*]  ..
```

Notes:

- Boolean operators are `&&` / `||` / `not` — not `and` / `or`.
- Equality/comparison, `in` / `not in`, `is` / `is not`, arithmetic (`+ - * / %`), unary minus,
  ternary (`x if cond else y`), list/dict literals, and indexing are all supported.
- Typecasts come in two forms: function-style `int(<expr>)` inline, or trailing `<expr> -> int`
  applied to the whole expression.
- Booleans are Python-style `True` / `False`, and null is `None` (not `true` / `null`).
