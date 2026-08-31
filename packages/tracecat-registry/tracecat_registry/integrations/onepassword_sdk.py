"""Generic interface for the 1Password Python SDK."""

import base64
import inspect
from collections.abc import AsyncIterator, Iterator, Mapping, Sequence
from enum import Enum
from typing import Annotated, Any, get_args, get_type_hints

from onepassword import Client
from pydantic import BaseModel, Field, TypeAdapter
from pydantic_core import to_jsonable_python

from tracecat_registry import RegistrySecret, __pep440_version__, registry, secrets
from tracecat_registry.config import TRACECAT__MAX_FILE_SIZE_BYTES

onepassword_secret = RegistrySecret(
    name="onepassword",
    keys=["OP_SERVICE_ACCOUNT_TOKEN"],
)
"""1Password service account token.

- name: `onepassword`
- keys:
    - `OP_SERVICE_ACCOUNT_TOKEN`

The wrapper authenticates explicitly with this token and does not use desktop
authorization or ambient credential discovery.
"""


async def _get_client() -> Client:
    return await Client.authenticate(
        auth=secrets.get("OP_SERVICE_ACCOUNT_TOKEN"),
        integration_name="Tracecat",
        integration_version=__pep440_version__,
    )


def _get_sdk_method(client: Client, service: str, method_name: str) -> Any:
    """Resolve a public method from a 1Password SDK service."""
    service_parts = service.split(".")
    if any(part.startswith("_") for part in service_parts) or method_name.startswith(
        "_"
    ):
        raise AttributeError(f"Unknown 1Password SDK method: {service}.{method_name}")

    error = AttributeError(f"Unknown 1Password SDK method: {service}.{method_name}")
    top_level_service, *nested_parts = service_parts
    expected_type = get_type_hints(Client).get(top_level_service)
    sdk_service: Any = getattr(client, top_level_service, None)
    if expected_type is None or not isinstance(sdk_service, expected_type):
        raise error

    service_module = type(sdk_service).__module__
    for part in nested_parts:
        if not (nested_service := getattr(sdk_service, part, None)):
            raise error
        nested_module = type(nested_service).__module__
        if not nested_module.startswith(f"{service_module}_"):
            raise error
        sdk_service = nested_service
        service_module = nested_module

    if (
        (method := getattr(sdk_service, method_name, None))
        and callable(method)
        and getattr(method, "__module__", None) == service_module
    ):
        return method
    raise error


def _contains_pydantic_model(annotation: Any) -> bool:
    if isinstance(annotation, type) and issubclass(annotation, BaseModel):
        return True
    return any(_contains_pydantic_model(arg) for arg in get_args(annotation))


def _coerce_value(annotation: Any, value: Any) -> Any:
    if (
        annotation is inspect.Parameter.empty
        or value is None
        or isinstance(value, BaseModel)
        or not _contains_pydantic_model(annotation)
        or not isinstance(value, Mapping | Sequence)
        or isinstance(value, str | bytes | bytearray)
    ):
        return value
    return TypeAdapter(annotation).validate_python(value)


def _prepare_call(
    method: Any,
    params: Mapping[str, Any],
) -> tuple[list[Any], dict[str, Any]]:
    """Build SDK arguments, including generated methods with variadic filters."""
    method_signature = inspect.signature(method)
    annotations = get_type_hints(method)
    remaining = dict(params)
    args: list[Any] = []

    variadic = next(
        (
            parameter
            for parameter in method_signature.parameters.values()
            if parameter.kind is inspect.Parameter.VAR_POSITIONAL
            and parameter.name in remaining
        ),
        None,
    )
    if variadic is not None:
        values = remaining.pop(variadic.name)
        if not isinstance(values, Sequence) or isinstance(
            values, str | bytes | bytearray
        ):
            raise TypeError(f"SDK parameter `{variadic.name}` must be a sequence.")

        for parameter in method_signature.parameters.values():
            if parameter is variadic:
                break
            if parameter.kind not in (
                inspect.Parameter.POSITIONAL_ONLY,
                inspect.Parameter.POSITIONAL_OR_KEYWORD,
            ):
                continue
            annotation = annotations.get(parameter.name, parameter.annotation)
            if parameter.name in remaining:
                args.append(_coerce_value(annotation, remaining.pop(parameter.name)))
            elif parameter.default is not inspect.Parameter.empty:
                args.append(parameter.default)
            else:
                raise TypeError(
                    f"Missing SDK parameter `{parameter.name}` required before "
                    f"`*{variadic.name}`."
                )

        annotation = annotations.get(variadic.name, variadic.annotation)
        args.extend(_coerce_value(annotation, value) for value in values)

    kwargs: dict[str, Any] = {}
    for name, value in remaining.items():
        parameter = method_signature.parameters.get(name)
        annotation = annotations.get(
            name,
            parameter.annotation if parameter is not None else inspect.Parameter.empty,
        )
        kwargs[name] = _coerce_value(annotation, value)
    return args, kwargs


def _serialize(value: Any) -> Any:
    """Adapt 1Password SDK values into JSON-serializable values."""
    if value is None or isinstance(value, str | int | float | bool):
        return value
    if isinstance(value, bytes | bytearray | memoryview):
        if len(value) > TRACECAT__MAX_FILE_SIZE_BYTES:
            raise ValueError(
                "1Password SDK binary response exceeds maximum size limit of "
                f"{TRACECAT__MAX_FILE_SIZE_BYTES // 1024 // 1024}MB"
            )
        return {"content_base64": base64.b64encode(bytes(value)).decode("ascii")}
    if isinstance(value, Enum):
        return _serialize(value.value)
    if isinstance(value, BaseModel):
        return _serialize(value.model_dump(by_alias=True, mode="json"))
    if isinstance(value, Mapping):
        return {str(key): _serialize(item) for key, item in value.items()}
    if isinstance(value, Sequence | set | frozenset):
        return [_serialize(item) for item in value]
    if isinstance(value, Iterator | AsyncIterator):
        raise TypeError(
            "The pinned 1Password SDK does not expose paginated iterator methods."
        )
    return to_jsonable_python(value)


@registry.register(
    default_title="Call method",
    description=(
        "Authenticate a 1Password service-account client and call a public SDK "
        "service method."
    ),
    display_group="1Password SDK",
    doc_url="https://github.com/1Password/onepassword-sdk-python",
    namespace="tools.onepassword_sdk",
    secrets=[onepassword_secret],
)
async def call_method(
    service: Annotated[
        str,
        Field(
            ...,
            description=(
                "1Password Client service path, for example `items`, `vaults`, "
                "`groups`, `secrets`, `environments`, or `items.files`."
            ),
        ),
    ],
    method_name: Annotated[
        str,
        Field(..., description="Public 1Password SDK method name."),
    ],
    params: Annotated[
        dict[str, Any] | None,
        Field(
            ...,
            description=(
                "1Password SDK method parameters. Dictionaries are validated "
                "against the SDK's generated Pydantic parameter types; values, "
                "including nulls, are otherwise forwarded unchanged. For a "
                "variadic SDK parameter such as `filters`, pass its values as a list."
            ),
        ),
    ] = None,
) -> Any:
    method = _get_sdk_method(await _get_client(), service, method_name)
    args, kwargs = _prepare_call(method, params or {})
    result = method(*args, **kwargs)
    if inspect.isawaitable(result):
        result = await result
    return _serialize(result)
