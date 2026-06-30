# Expressions in action args

Action `args` reference runtime data with template expressions: `${{ <namespace>.<path> }}`.
The value is resolved when the workflow runs. Three things commonly trip up authoring.

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
