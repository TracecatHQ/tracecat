#!/usr/bin/env python3
# /// script
# requires-python = ">=3.12"
# dependencies = [
#     "openapi-pydantic==0.5.1",
#     "pyyaml==6.0.2",
#     "tracecat",
#     "tracecat_registry",
# ]
# [tool.uv.sources]
# tracecat = { path = "../" }
# tracecat_registry = { path = "../registry" }
# ///
"""
OpenAPI to Tracecat Action Templates Generator

This script generates Tracecat HTTP action templates from an OpenAPI specification.
It creates a template action for each operation in the OpenAPI spec, with appropriate
input schemas, HTTP steps, and documentation links.

Usage:
    # Installs dependencies
    uv run openapi_to_template.py --input openapi.json --output-dir templates/ --config gen_template_config.yaml

Tested with msgraph, sentinelone, and jira.
"""

import argparse
import fnmatch
import json
import os
import re
from typing import Any

import yaml
from openapi_pydantic.v3.v3_0 import (
    DataType,
    ExternalDocumentation,
    Info,
    MediaType,
    OpenAPI,
    Operation,
    Parameter,
    PathItem,
    Paths,
    Reference,
    RequestBody,
    Schema,
)
from pydantic import BaseModel, Field

from tracecat.expressions.expectations import ExpectedField
from tracecat.registry.actions.models import (
    ActionStep,
    RegistrySecret,
    TemplateAction,
    TemplateActionDefinition,
)


class PathMatchers(BaseModel):
    """
    Path matchers for including or excluding endpoints.

    Attributes:
        like: List of glob patterns to match paths (e.g., "/pet/{petId}*").
        exact: List of exact path matches (e.g., "/pet/123").
    """

    like: list[str] | None = Field(default_factory=list, examples=[["/pet/{petId}*"]])
    exact: list[str] | None = Field(default_factory=list, examples=[["/pet/123"]])


class IncludeExcludePaths(BaseModel):
    """
    Configuration for including or excluding endpoint paths.

    Attributes:
        include: PathMatchers for endpoints to include.
        exclude: PathMatchers for endpoints to exclude.
    """

    include: PathMatchers | None = Field(
        default=None, examples=[{"like": ["/pet/{petId}*"], "exact": ["/pet/123"]}]
    )
    exclude: PathMatchers | None = Field(
        default=None, examples=[{"like": ["/store/*"], "exact": ["/store/order/1"]}]
    )


class DefinitionOverrides(BaseModel):
    """
    Overrides for action definition fields.

    Attributes:
        display_group: Display group for the action (e.g., "PetStore").
        namespace: Namespace for the action (e.g., "the.pet.store").
        author: Author of the action (e.g., "OpenAPI Generator").
        doc_url_prefix: Prefix for documentation URLs (e.g., "https://petstore.swagger.io/docs").
        name: Name override for the action (e.g., "delete_pet").
        title: Title override for the action (e.g., "Deletes a pet").
        description: Description override for the action.
        doc_url: Documentation URL override.
        deprecated: Deprecation message or flag.
    """

    display_group: str | None = Field(default=None, examples=["PetStore"])
    namespace: str | None = Field(default=None, examples=["the.pet.store"])
    author: str | None = Field(default=None, examples=["OpenAPI Generator"])
    doc_url_prefix: str | None = Field(
        default=None, examples=["https://petstore.swagger.io/docs"]
    )
    name: str | None = Field(default=None, examples=["delete_pet"])
    title: str | None = Field(default=None, examples=["Deletes a pet"])
    description: str | None = Field(
        default=None, examples=["HTTP DELETE request to /pet/{petId}"]
    )
    doc_url: str | None = Field(
        default=None, examples=["https://petstore.swagger.io/v2"]
    )
    deprecated: str | None = Field(
        default=None, examples=["This action is deprecated."]
    )

    # Allow any other string key-value pairs for flexibility to override any field in TemplateActionDefinition
    model_config = {"extra": "allow"}


class AuthSecretDefinition(BaseModel):
    """
    Definition of a secret used for authentication.

    Attributes:
        name: Name of the secret (e.g., "petstore").
        keys: List of required keys for the secret (e.g., ["API_KEY"]).
    """

    name: str = Field(..., examples=["petstore"])
    keys: list[str] = Field(..., examples=[["API_KEY"]])


class AuthInjectionArgs(BaseModel):
    """
    Arguments for injecting authentication into requests.

    Attributes:
        headers: Headers to inject (e.g., {"Authorization": "ApiKey ${{ SECRETS.petstore.API_KEY }}"}).
        params: Query parameters to inject (e.g., {"api_key": "${{ SECRETS.petstore.API_KEY }}"}).
    """

    headers: dict[str, str] | None = Field(
        default_factory=dict,
        examples=[{"Authorization": "ApiKey ${{ SECRETS.petstore.API_KEY }}"}],
    )
    params: dict[str, str] | None = Field(
        default_factory=dict, examples=[{"api_key": "${{ SECRETS.petstore.API_KEY }}"}]
    )


class AuthInjection(BaseModel):
    """
    Authentication injection configuration.

    Attributes:
        args: Arguments for injection (headers, params).
    """

    args: AuthInjectionArgs | None = Field(
        default=None,
        examples=[
            {
                "args": {
                    "headers": {
                        "Authorization": "ApiKey ${{ SECRETS.petstore.API_KEY }}"
                    },
                    "params": {"api_key": "${{ SECRETS.petstore.API_KEY }}"},
                }
            }
        ],
    )


class AuthConfig(BaseModel):
    """
    Authentication configuration for the generator.

    Attributes:
        secrets: List of secret definitions.
        injection: Injection configuration.
        expects: Expected fields for authentication.
    """

    secrets: list[AuthSecretDefinition] | None = Field(
        default_factory=list, examples=[[{"name": "petstore", "keys": ["API_KEY"]}]]
    )
    injection: AuthInjection | None = Field(
        default=None,
        examples=[
            {
                "args": {
                    "headers": {
                        "Authorization": "ApiKey ${{ SECRETS.petstore.API_KEY }}"
                    },
                    "params": {"api_key": "${{ SECRETS.petstore.API_KEY }}"},
                }
            }
        ],
    )
    expects: dict[str, ExpectedField] | None = Field(
        default_factory=dict,
        examples=[
            {
                "api_key": {
                    "type": "str",
                    "description": "API key for authentication",
                    "default": None,
                }
            }
        ],
    )


class GeneratorConfig(BaseModel):
    """
    Main configuration for the OpenAPI to Tracecat generator.

    Attributes:
        endpoints: Include/exclude path configuration.
        definition_overrides: Overrides for action definitions.
        auth: Authentication configuration.
        use_namespace_directories: Create subdirectories based on action namespace (e.g., api/pets/action.yml). If False, actions are placed directly in the output directory.
    """

    endpoints: IncludeExcludePaths | None = Field(
        default=None,
        examples=[
            {
                "include": {"like": ["/pet/{petId}*"], "exact": ["/pet/123"]},
                "exclude": {"like": ["/store/*"], "exact": ["/store/order/1"]},
            }
        ],
    )
    definition_overrides: DefinitionOverrides | None = Field(
        default=None,
        examples=[
            {
                "display_group": "PetStore",
                "namespace": "the.pet.store",
                "author": "OpenAPI Generator",
                "doc_url_prefix": "https://petstore.swagger.io/docs",
                "name": "delete_pet",
                "title": "Deletes a pet",
                "description": "HTTP DELETE request to /pet/{petId}",
                "doc_url": "https://petstore.swagger.io/v2",
                "deprecated": "This action is deprecated.",
            }
        ],
    )
    auth: AuthConfig | None = Field(
        default=None,
        examples=[
            {
                "secrets": [{"name": "petstore", "keys": ["API_KEY"]}],
                "injection": {
                    "args": {
                        "headers": {
                            "Authorization": "ApiKey ${{ SECRETS.petstore.API_KEY }}"
                        },
                        "params": {"api_key": "${{ SECRETS.petstore.API_KEY }}"},
                    }
                },
                "expects": {
                    "api_key": {
                        "type": "str",
                        "description": "API key for authentication",
                        "default": None,
                    }
                },
            }
        ],
    )
    use_namespace_directories: bool = Field(
        default=True,
        description="Create subdirectories based on action namespace (e.g., api/pets/action.yml). If False, actions are placed directly in the output directory.",
        examples=[True, False],
    )


# NEW FUNCTION: Pre-processor for OpenAPI spec data
def preprocess_openapi_spec_data(data: Any) -> Any:
    """
    Recursively traverses the OpenAPI spec data (loaded as a dictionary/list)
    and corrects known non-standard entries.
    Currently focuses on fixing boolean parameter names by converting them to strings.
    """
    if isinstance(data, dict):
        new_dict = {}
        for key, value in data.items():
            # Specifically target parameter definitions for name correction
            if key == "parameters" and isinstance(value, list):
                new_parameters_list = []
                for param_obj in value:
                    if isinstance(param_obj, dict) and "name" in param_obj:
                        param_name = param_obj["name"]
                        if isinstance(param_name, bool):
                            corrected_name = str(param_name)
                            # Create a copy to modify, or modify in place if original data modification is fine
                            # For safety, let's create a new dict for the param_obj
                            modified_param_obj = param_obj.copy()
                            modified_param_obj["name"] = corrected_name
                            print(
                                f"Warning: Corrected non-standard parameter name '{param_name}' (type: {type(param_name).__name__}) "
                                f"to string '{corrected_name}' in path/operation: (path details not available here, check logs)"
                            )
                            new_parameters_list.append(
                                preprocess_openapi_spec_data(modified_param_obj)
                            )
                        else:
                            new_parameters_list.append(
                                preprocess_openapi_spec_data(param_obj)
                            )
                    else:
                        # Handle cases where param_obj might not be a dict or not have a 'name'
                        new_parameters_list.append(
                            preprocess_openapi_spec_data(param_obj)
                        )
                new_dict[key] = new_parameters_list
            else:
                new_dict[key] = preprocess_openapi_spec_data(value)
        return new_dict
    elif isinstance(data, list):
        return [preprocess_openapi_spec_data(item) for item in data]
    return data


# Standard HTTP methods based on OpenAPI PathItem fields
HTTP_METHODS = {"get", "put", "post", "delete", "patch", "options", "head", "trace"}


def sanitize_name(name: str) -> str:
    """Convert a string to a valid Tracecat action name."""
    # Replace any non-alphanumeric characters with underscores
    sanitized = re.sub(r"[^a-zA-Z0-9_]", "_", name)
    # Ensure it starts with a letter or underscore
    if sanitized and sanitized[0].isdigit():
        sanitized = f"_{sanitized}"
    # Convert to snake_case if it's in camelCase
    if any(c.isupper() for c in sanitized):
        sanitized = re.sub(r"([a-z0-9])([A-Z])", r"\1_\2", sanitized).lower()
    return sanitized


def openapi_type_to_tracecat_type(
    schema_type: DataType | list[DataType] | None, schema_format: str | None = None
) -> str:
    """Convert OpenAPI type to Tracecat type."""
    if schema_type == "integer":
        return "int"
    elif schema_type == "number":
        return "float"
    elif schema_type == "boolean":
        return "bool"
    elif schema_type == "string":
        if schema_format == "date-time":
            return "datetime"
        return "str"
    elif schema_type == "array":
        return "list[any]"  # We'll refine this later if we have item type information
    elif schema_type == "object":
        return "dict[str, any]"
    elif (
        schema_type == "null" or schema_type is None
    ):  # Handle cases where type might be None
        return "None"
    else:
        return "any"


def process_parameter(param: Parameter | Reference) -> tuple[str, ExpectedField]:
    """Process an OpenAPI parameter into a Tracecat ExpectedField."""
    if isinstance(param, Reference):
        # TODO: Implement reference resolving if necessary, for now, skip or raise
        raise NotImplementedError(
            f"Reference parameters are not yet supported: {param.ref}"
        )

    name = param.name
    # Parameter.schema_ is the Pydantic model attribute for the 'schema' field
    schema_obj: Schema | Reference | None = param.param_schema

    if isinstance(schema_obj, Reference):
        # TODO: Implement reference resolving for schema if necessary
        raise NotImplementedError(
            f"Schema reference in parameter {name} is not yet supported: {schema_obj.ref}"
        )

    schema_type = None
    schema_format = None
    description: str | None = param.description
    default_value: Any = None

    if schema_obj:  # schema_obj is now Schema | None
        # Check if schema_obj.anyOf is a list before trying to access its elements
        if schema_obj.anyOf and isinstance(schema_obj.anyOf, list) and schema_obj.anyOf:
            # Handle anyOf by taking the first schema as primary
            primary_schema_ref_or_schema = schema_obj.anyOf[0]
            if isinstance(primary_schema_ref_or_schema, Reference):
                raise NotImplementedError(
                    f"Schema reference in anyOf for parameter {name} is not yet supported: {primary_schema_ref_or_schema.ref}"
                )
            # Now primary_schema_ref_or_schema is a Schema object
            primary_schema: Schema = primary_schema_ref_or_schema
            schema_type = primary_schema.type
            schema_format = primary_schema.schema_format
            if primary_schema.title and not description:
                description = primary_schema.title
            default_value = primary_schema.default
        else:
            schema_type = schema_obj.type
            schema_format = schema_obj.schema_format
            if schema_obj.title and not description:
                description = schema_obj.title
            default_value = schema_obj.default

    tracecat_type = openapi_type_to_tracecat_type(schema_type, schema_format)

    # If it's an array with items, specify the item type
    if schema_type == "array" and schema_obj and schema_obj.items:
        items_schema_ref_or_schema = schema_obj.items
        if isinstance(items_schema_ref_or_schema, Reference):
            # TODO: Handle reference if necessary
            pass  # Keep tracecat_type as list[any] for now
        # items_schema_ref_or_schema is Schema here
        elif items_schema_ref_or_schema.type:
            item_type = openapi_type_to_tracecat_type(items_schema_ref_or_schema.type)
            tracecat_type = f"list[{item_type}]"

    required = param.required
    # Default should only be applied if not required
    default_to_use = default_value if not required else None

    expected_field = ExpectedField(
        type=tracecat_type, description=description, default=default_to_use
    )

    return name, expected_field


def create_http_step(
    path: str,
    method: str,
    operation: Operation,  # Changed from operation_id and parameters
    has_request_body: bool,
    auth_config: AuthConfig | None = None,
) -> ActionStep:
    """Create an HTTP step for the Tracecat action template."""
    # Organize parameters by location
    path_params: list[str] = []
    query_params: list[str] = []
    header_params: list[str] = []

    if operation.parameters:
        for param_ref_or_obj in operation.parameters:
            if isinstance(param_ref_or_obj, Reference):
                # TODO: Handle reference or raise error
                print(
                    f"Warning: Skipping reference parameter {param_ref_or_obj.ref} in create_http_step"
                )
                continue
            param_obj: Parameter = param_ref_or_obj
            param_in = param_obj.param_in
            if param_in == "path":
                path_params.append(param_obj.name)
            elif param_in == "query":
                query_params.append(param_obj.name)
            elif param_in == "header":
                header_params.append(param_obj.name)

    # Prepare the URL path segment by substituting path parameters
    url_path_segment = path
    if path_params:
        for param_name in path_params:
            url_path_segment = url_path_segment.replace(
                f"{{{param_name}}}", f"${{{{ inputs.{param_name} }}}}"
            )

    # Prepare arguments for the HTTP request step
    args: dict[str, Any] = {
        "method": method.upper(),
    }

    # Construct URL from base_url input and path segment
    args["url"] = f"${{{{ inputs.base_url }}}}{url_path_segment}"

    # Prepare query parameters
    final_query_params: dict[str, Any] = {}
    if query_params:  # Parameters from OpenAPI spec
        for param_name in query_params:
            final_query_params[param_name] = f"${{{{ inputs.{param_name} }}}}"

    # Override or add query parameters from auth_config
    if (
        auth_config
        and auth_config.injection
        and auth_config.injection.args
        and auth_config.injection.args.params
    ):
        final_query_params.update(auth_config.injection.args.params)

    if final_query_params:
        args["params"] = final_query_params

    # Prepare headers
    final_headers: dict[str, str] = {}
    # Start with headers from OpenAPI spec parameters
    if operation.parameters:
        for param_ref_or_obj in operation.parameters:
            if (
                isinstance(param_ref_or_obj, Parameter)
                and param_ref_or_obj.param_in == "header"
            ):
                # Use the original name from spec for the key to preserve casing
                final_headers[param_ref_or_obj.name] = (
                    f"${{{{ inputs.{param_ref_or_obj.name} }}}}"
                )

    # Apply default Authorization header if no auth_config is provided or if auth_config doesn't define Authorization
    # And if 'Authorization' wasn't already defined by a spec parameter.
    apply_default_auth_header = True
    if (
        auth_config
        and auth_config.injection
        and auth_config.injection.args
        and auth_config.injection.args.headers
    ):
        if "Authorization" in auth_config.injection.args.headers:
            apply_default_auth_header = False  # Auth config handles Authorization

    if apply_default_auth_header and "Authorization" not in final_headers:
        # This default is only added if not handled by spec or specific auth_config injection
        # And only if there's no auth.secrets defined (checked in process_operation)
        # However, process_operation decides whether to add `inputs.auth_header`
        # If `inputs.auth_header` is expected, we should add this header.
        # This part needs careful coordination with expects["auth_header"] decision.
        # For now, let's assume if auth_header is in inputs, it should be used if no other auth is overriding.
        # This will be clearer when process_operation is fully updated.
        final_headers["Authorization"] = (
            "${{ inputs.auth_header }}"  # Tentative, depends on expects
        )

    # Override or add headers from auth_config (takes precedence)
    if (
        auth_config
        and auth_config.injection
        and auth_config.injection.args
        and auth_config.injection.args.headers
    ):
        final_headers.update(auth_config.injection.args.headers)

    if final_headers:  # Only add headers argument if there are any headers
        args["headers"] = final_headers

    # Add request body if needed
    if has_request_body:
        args["json"] = "${{ inputs.body }}"

    return ActionStep(ref="http_call", action="core.http_request", args=args)


def process_operation(  # Added type hints from openapi-pydantic
    path: str,
    method: str,
    operation: Operation,
    api_info: Info,
    config: GeneratorConfig | None,
) -> TemplateAction:
    """Process an OpenAPI operation into a Tracecat action template."""
    operation_id = operation.operationId or f"{method}_{sanitize_name(path)}"

    # Get operation metadata
    summary = operation.summary or f"{method.upper()} {path}"
    description = operation.description or f"HTTP {method.upper()} request to {path}"
    if len(description) >= 1000:
        description = description[:996] + "..."
    tags = operation.tags or ["api"]

    # --- Initialize with default values or values from API spec ---
    action_name = sanitize_name(operation_id)

    # Determine the suffix part from the primary tag for potential appending.
    # This is the sanitized version of the first tag (e.g., "user", "pet_store").
    tag_suffix_for_append = sanitize_name(tags[0])

    # Initialize action_namespace based on tags.
    # Avoids "api.api" if the tag was "api" due to default or explicit single "api" tag.
    if tag_suffix_for_append == "api" and tags == ["api"]:
        action_namespace = "api"
    else:
        action_namespace = f"api.{tag_suffix_for_append}"

    action_title = summary
    action_description = description
    action_display_group = tags[0] if tags else "API"
    action_author = "OpenAPI Generator"
    action_doc_url: str | None = None

    op_external_docs: ExternalDocumentation | None = operation.externalDocs
    api_external_docs: ExternalDocumentation | None = getattr(
        api_info, "externalDocs", None
    )
    if op_external_docs and op_external_docs.url:
        action_doc_url = str(op_external_docs.url)
    elif api_external_docs and api_external_docs.url:
        action_doc_url = str(api_external_docs.url)

    action_secrets: list[AuthSecretDefinition] | None = (
        None  # For TemplateActionDefinition
    )
    final_expects: dict[str, ExpectedField] = {}

    # --- Apply definition_overrides from config ---
    if config and config.definition_overrides:
        overrides = config.definition_overrides
        if overrides.name:  # Assuming 'name' can be overridden, though less common
            action_name = sanitize_name(overrides.name)

        if overrides.namespace:  # This is the config_override_prefix
            config_override_prefix = overrides.namespace
            # Check if the tag_suffix_for_append is meaningful (not just the default "api")
            if tag_suffix_for_append == "api" and tags == ["api"]:
                # If the original tag was effectively the default "api" (i.e., no specific meaningful tag),
                # then the config override fully replaces the namespace.
                action_namespace = config_override_prefix
            else:
                # A meaningful tag_suffix_for_append exists (e.g., "user", "pet_store"). Append it to the prefix.
                action_namespace = f"{config_override_prefix}.{tag_suffix_for_append}"

        if overrides.title:  # Assuming 'title' can be overridden
            action_title = overrides.title
        if overrides.description:
            action_description = overrides.description
        if overrides.display_group:
            action_display_group = overrides.display_group
        if overrides.author:
            action_author = overrides.author
        # For doc_url, we might want a prefix or a full override
        if overrides.doc_url:  # Direct override
            action_doc_url = overrides.doc_url
        elif overrides.doc_url_prefix and action_doc_url:  # Prepend prefix
            action_doc_url = (
                f"{overrides.doc_url_prefix.rstrip('/')}/{action_doc_url.lstrip('/')}"
            )
        elif overrides.doc_url_prefix:  # Use prefix as doc_url if no base doc_url found
            action_doc_url = overrides.doc_url_prefix
        # Allow overriding other fields via model_extra in DefinitionOverrides Pydantic model if needed later

    # --- Process parameters and auth config for 'expects' and 'secrets' ---
    # Add base URL parameter (always expected)
    final_expects["base_url"] = ExpectedField(
        type="str",
        description=f"Base URL for the {api_info.title or 'API'}",
        default=None,  # Default could also come from a global config setting if desired
    )

    # Auth handling
    auth_active_from_config = False
    current_auth_config: AuthConfig | None = None
    if config and config.auth:
        current_auth_config = config.auth
        if current_auth_config.secrets:
            auth_active_from_config = True
            action_secrets = current_auth_config.secrets  # Use secrets from config
        if current_auth_config.expects:
            auth_active_from_config = True  # Even if only expects are defined for auth
            final_expects.update(current_auth_config.expects)

    # Add default 'auth_header' input ONLY if no auth is configured via gen-config
    if not auth_active_from_config:
        final_expects["auth_header"] = ExpectedField(
            type="str",
            description="Authorization header value (e.g., 'Bearer token123')",
            default=None,
        )

    # Process path, query, and header parameters from OpenAPI spec
    if operation.parameters:
        for param_ref_or_obj in operation.parameters:
            try:
                name, expected_field = process_parameter(param_ref_or_obj)
                # Do not override auth-related expects if they came from auth_config
                if (
                    name in final_expects
                    and auth_active_from_config
                    and current_auth_config
                    and current_auth_config.expects
                    and name in current_auth_config.expects
                ):
                    print(
                        f"Parameter '{name}' from OpenAPI spec conflicts with auth_config.expects. Using auth_config version."
                    )
                    continue
                final_expects[name] = expected_field
            except NotImplementedError as e:
                print(f"Skipping parameter due to unsupported feature: {e}")

    # Check for request body (from OpenAPI spec)
    has_request_body = False
    if operation.requestBody:
        request_body_ref_or_obj = operation.requestBody
        if isinstance(request_body_ref_or_obj, Reference):
            # TODO: Implement reference resolving if necessary
            print(
                f"Warning: Skipping reference request body {request_body_ref_or_obj.ref}"
            )
        else:
            request_body_obj: RequestBody = request_body_ref_or_obj
            has_request_body = True  # Mark as true if RequestBody object exists
            # Ensure content is not None and is a dictionary
            if request_body_obj.content and isinstance(request_body_obj.content, dict):
                json_media_type: MediaType | None = request_body_obj.content.get(
                    "application/json"
                )
                # Check if json_media_type is not None and has a schema
                if json_media_type and json_media_type.media_type_schema:
                    # The schema_ attribute could be a Reference or a Schema object
                    # For simplicity, we are not resolving References here
                    pass  # Type information for body is generic dict[str, any]

            final_expects["body"] = ExpectedField(
                type="dict[str, any]",
                description="Request body",
                default=None if request_body_obj.required else {},
            )

    # Create HTTP step, passing the relevant part of the auth config
    http_step = create_http_step(
        path=path,
        method=method,
        operation=operation,
        has_request_body=has_request_body,
        auth_config=current_auth_config,  # Pass the auth part of the config
    )

    # Create the template definition using processed values
    # Convert AuthSecretDefinition to RegistrySecret if secrets are present
    final_action_secrets: list[RegistrySecret] | None = None
    if action_secrets:
        final_action_secrets = [
            RegistrySecret(name=s.name, keys=s.keys) for s in action_secrets
        ]

    template_def = TemplateActionDefinition(
        name=action_name,
        namespace=action_namespace,
        title=action_title,
        description=action_description,
        display_group=action_display_group,
        doc_url=action_doc_url,
        author=action_author,
        secrets=final_action_secrets,  # Pass the converted list
        expects=final_expects,
        steps=[http_step],
        returns="${{ steps.http_call.result.data }}",
        # Apply other overrides if they exist in the config and are part of TemplateActionDefinition
        # Note: TemplateActionDefinition.deprecated might expect a string by some linters/definitions.
        # Here, we assume a boolean from config is acceptable, or it should be a string in gen-config.yaml.
        deprecated=(
            config.definition_overrides.deprecated
            if config
            and config.definition_overrides
            and config.definition_overrides.deprecated is not None
            else None
        ),
    )

    return TemplateAction(type="action", definition=template_def)


def generate_tracecat_templates(
    openapi_spec: OpenAPI, output_dir: str, config: GeneratorConfig | None
) -> None:  # Changed type hint
    """Generate Tracecat action templates from an OpenAPI spec."""
    # Create output directory if it doesn't exist
    os.makedirs(output_dir, exist_ok=True)

    # Extract API info
    api_info: Info = openapi_spec.info  # Directly use the Info object

    # Process paths and operations
    # openapi_spec.paths is Optional[Paths], where Paths is Dict[str, PathItem | Reference]
    spec_paths: Paths | None = openapi_spec.paths
    templates_generated = 0

    if spec_paths:  # Check if spec_paths is not None
        for path_str, path_item_ref_or_obj in spec_paths.items():
            if isinstance(path_item_ref_or_obj, Reference):
                # TODO: Handle Reference path items if necessary
                print(f"Skipping reference path item: {path_item_ref_or_obj.ref}")
                continue

            # Endpoint filtering based on config
            if config and config.endpoints:
                path_included = False
                # Check include rules first
                if config.endpoints.include:
                    if (
                        config.endpoints.include.exact
                        and path_str in config.endpoints.include.exact
                    ):
                        path_included = True
                    if not path_included and config.endpoints.include.like:
                        for pattern in config.endpoints.include.like:
                            if fnmatch.fnmatch(path_str, pattern):
                                path_included = True
                                break
                else:
                    # If no include rules, all paths are considered for inclusion by default
                    path_included = True

                # Check exclude rules if path was included
                if path_included and config.endpoints.exclude:
                    if (
                        config.endpoints.exclude.exact
                        and path_str in config.endpoints.exclude.exact
                    ):
                        path_included = False
                    if path_included and config.endpoints.exclude.like:
                        for pattern in config.endpoints.exclude.like:
                            if fnmatch.fnmatch(path_str, pattern):
                                path_included = False
                                break

                if not path_included:
                    print(
                        f"Skipping path {path_str} due to endpoint filter configuration."
                    )
                    continue

            path_item_obj: PathItem = path_item_ref_or_obj
            # Iterate over HTTP methods defined in PathItem
            # These are get, post, put, delete, patch, etc.
            # PathItem model fields include these methods directly.
            # We iterate through a predefined set of known HTTP methods
            # to ensure we only process operation-defining fields.
            for method_name in HTTP_METHODS:
                operation_obj: Operation | Reference | None = getattr(
                    path_item_obj, method_name, None
                )

                if isinstance(operation_obj, Reference):
                    print(
                        f"Skipping reference operation for {method_name.upper()} {path_str}: {operation_obj.ref}"
                    )
                    continue

                if (
                    operation_obj
                ):  # Ensure operation_obj is not None and is an Operation
                    # Process the operation
                    try:
                        template = process_operation(
                            path_str, method_name, operation_obj, api_info, config
                        )

                        # Determine output path based on configuration
                        if config and config.use_namespace_directories is False:
                            category_dir = output_dir
                        else:
                            namespace_parts = template.definition.namespace.split(".")
                            category_dir = os.path.join(output_dir, *namespace_parts)

                        os.makedirs(category_dir, exist_ok=True)

                        # Write the template to a file with controlled order
                        output_path = os.path.join(
                            category_dir, f"{template.definition.name}.yml"
                        )

                        # Manually order the fields for YAML output
                        template_data = template.model_dump(by_alias=True)
                        definition_data = template_data.get("definition", {})

                        # Define the desired order for definition fields
                        # Ensure all fields from the model are considered.
                        all_definition_keys = list(
                            TemplateActionDefinition.model_fields.keys()
                        )
                        # Start with a predefined order for common fields
                        preferred_order = [
                            "name",
                            "namespace",
                            "title",
                            "description",
                            "author",
                            "display_group",
                            "doc_url",
                            "deprecated",
                            "secrets",
                            "expects",
                            "steps",
                            "returns",  # returns is now after steps here
                        ]

                        ordered_definition_keys = []
                        present_definition_keys = set(definition_data.keys())

                        # Add preferred keys in order if they exist
                        for key in preferred_order:
                            if key in present_definition_keys:
                                ordered_definition_keys.append(key)
                                present_definition_keys.remove(key)

                        # Add any remaining keys from the model (e.g. new fields not in preferred_order)
                        # that were actually present in definition_data, sorted for consistency
                        remaining_model_keys = [
                            k
                            for k in all_definition_keys
                            if k in present_definition_keys
                        ]
                        ordered_definition_keys.extend(sorted(remaining_model_keys))

                        ordered_definition_content = {
                            key: definition_data[key]
                            for key in ordered_definition_keys
                            if key in definition_data  # Ensure key exists before access
                        }

                        final_yaml_data = {
                            "type": template_data.get(
                                "type", "action"
                            ),  # Ensure type is first
                            "definition": ordered_definition_content,
                        }
                        # Add any other top-level keys from template_data besides 'type' and 'definition'
                        # (though TemplateAction only has type and definition currently)
                        for key, value in template_data.items():
                            if key not in final_yaml_data:
                                final_yaml_data[key] = value

                        with open(output_path, "w") as f:
                            yaml.dump(
                                final_yaml_data,
                                f,
                                sort_keys=False,  # Crucial for preserving our order
                                default_flow_style=False,
                                indent=2,
                                allow_unicode=True,  # Good practice
                            )

                        templates_generated += 1
                        print(f"Generated template: {output_path}")
                    except NotImplementedError as e:
                        print(
                            f"Skipping operation {method_name.upper()} {path_str} due to: {e}"
                        )
                    except Exception as e:
                        print(
                            f"Error processing operation {method_name.upper()} {path_str}: {e}"
                        )

    print(f"\nGenerated {templates_generated} templates.")


def main():
    """Main entry point for the script."""
    parser = argparse.ArgumentParser(
        description="Generate Tracecat action templates from an OpenAPI spec"
    )
    parser.add_argument(
        "--input", required=True, help="Path to the OpenAPI spec file (JSON or YAML)"
    )
    parser.add_argument(
        "--output-dir",
        default="tracecat_templates",
        help="Directory to output the templates (default: tracecat_templates)",
    )
    # Add new argument for the generator config
    parser.add_argument(
        "--config",
        help="Path to the generator config YAML file (optional)",
        default=None,  # Ensure it's truly optional
    )

    args = parser.parse_args()

    # Load generator config if provided
    config: GeneratorConfig | None = None
    if args.config:
        try:
            with open(args.config) as f:
                config_data = yaml.safe_load(f)
            config = GeneratorConfig.model_validate(config_data)
            print(f"Loaded generator configuration from: {args.config}")
        except FileNotFoundError:
            print(
                f"Warning: Generator config file not found: {args.config}. Proceeding without it."
            )
        except Exception as e:
            print(
                f"Error loading or parsing generator config: {e}. Proceeding without it."
            )

    # Load the OpenAPI spec
    spec: OpenAPI | None = None  # Initialize spec to None
    try:
        raw_spec_data: dict[str, Any] | None = None  # Ensure it's initialized
        print(f"Loading OpenAPI specification from: {args.input}")
        with open(args.input, encoding="utf-8") as f:
            content = f.read()
            if args.input.endswith(".json"):
                raw_spec_data = json.loads(content)
            elif args.input.endswith((".yml", ".yaml")):
                raw_spec_data = yaml.safe_load(content)
            else:
                print("Error: Input file must be a JSON or YAML file.")
                return  # Exit if not a valid file type

        if raw_spec_data:
            print("Attempting to preprocess the OpenAPI specification...")
            # Pre-process the raw data
            corrected_spec_data = preprocess_openapi_spec_data(raw_spec_data)
            print("Pre-processing complete. Validating corrected specification...")
            # Validate the corrected data
            spec = OpenAPI.model_validate(corrected_spec_data)
            print(
                "OpenAPI specification loaded and validated successfully after pre-processing."
            )
        else:
            print("Error: Could not load raw OpenAPI spec data from input file.")
            return

    except FileNotFoundError:
        print(f"Error: Input OpenAPI spec file not found at {args.input}")
        return
    except json.JSONDecodeError as e:
        print(f"Error decoding JSON from OpenAPI spec file {args.input}: {e}")
        return
    except yaml.YAMLError as e:
        print(f"Error parsing YAML from OpenAPI spec file {args.input}: {e}")
        return
    except Exception as e:
        print(f"Error loading, pre-processing, or parsing OpenAPI spec: {e}")
        # For Pydantic validation errors, the error 'e' should contain detailed information.
        # Consider adding more specific error handling for pydantic.ValidationError if needed.
        return  # Exit on error

    # Generate the templates
    if spec:  # Ensure spec is loaded successfully
        generate_tracecat_templates(spec, args.output_dir, config)


if __name__ == "__main__":
    main()
