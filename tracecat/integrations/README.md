# Integrations

## Creating a new integration

1. [Optional] Create a new integration platform/namespace and add a new platform icon in `frontend/src/components/icons.tsx`
2. Create a new integration function in an integration namespace.
3. Import the `registry` singleton from `tracecat/integrations/_registry.py` and register the integration function using `@registry.register(...)`.
4. [IMPORTANT] Import the integration nodule into the scope of `integrations/__init__.py`. This eagerly registers all integrations in this module with the registry.
5. Update `ActionType` in types/actions.py
6. (Frontend) Update `integrationTypes` in frontend/src/types/schemas.ts

Once this is done, the integration should be available in the frontend and backend and can be used like any other action node.

## Support

We explicitly don't support highly complex input types. This includes complex union types and heavily nested types, for example `list[str] | str | int | None` would be unsupported.
We also don't support generics and type variables. All types must be concrete. We use Python 3.10+ syntax for type annotations.

Let `T`, `K`, and `V` be a supported builtin types. We have well-defined support for the following data types, for example:

- Builtins: `str`, `int`, `float`, `bool`
- Optional: `T | None`
- Defaults: `T | None = None`, `str = "default"`
- Literals: `Literal["a", "b"]` (imported from typing.Literal. Use this for enums)
- List: `list[T]`, `list[T] | None = None`
- Dict: `dict[K, V]`, `dict[K, V] | None = None`

This should cover most use cases for integration API endpoints.

## Glossary

- Integration key/qualname: A fully qualified, unique idenfitier for an integration function. The schema is `integrations.<platform>.[<optional_namespaces>.]<fn_name>`.
  - For example:
    - `integrations.github.get_repo`
    - `integrations.github.actions.get_workflow_run`.
