import asyncio
import textwrap
from pathlib import Path
from typing import Annotated, Any

import httpx
from mcp.server.fastmcp import FastMCP
from pydantic import Field

from tracecat.dsl.common import DSLInput
from tracecat_mcp.utils import get_client, manager, validate_wf_defn_yaml_path

mcp = FastMCP(
    "tracecat-mcp-server",
    instructions="You are an AI agent built by Tracecat, a cybersecurity automation platform.",
)


ActionId = Annotated[
    str,
    Field(
        description="The unique identifier of the action. e.g. core.transform.reshape"
    ),
]


@mcp.tool(
    name="list_available_expression_functions",
    description="A list of all available expression functions.",
)
async def list_available_expression_functions() -> list[dict[str, Any]]:
    """Get a list of all available expression functions."""
    async with get_client() as client:
        response = await client.get("/editor/functions")
    response.raise_for_status()
    return response.json()


# @mcp.tool(
#     name="get_expression_grammar",
#     description="A comprehensive XML reference for the Tracecat Expression DSL, including syntax, contexts (ACTIONS, TRIGGER, SECRETS, FN), operators, typecasting, available functions, and the full Lark grammar. Suitable for LLM consumption.",
# )
# async def get_expression_grammar() -> str:
#     """Get the grammar and a comprehensive reference of the expression DSL used in Tracecat."""
#     async with get_client() as client:
#         response = await client.get("/builder/resources/grammar")
#     response.raise_for_status()
#     match raw_data := response.json():
#         case {
#             "id": "grammar",
#             "content_type": "application/vnd.tc+json",
#             "data": {
#                 "expressions": expressions_grammar,
#                 "expectations": expectations_grammar,
#             },
#         }:
#             pass
#         case _:
#             raise ValueError(f"Unexpected content type: {raw_data}")
#     xml_reference = textwrap.dedent(
#         f"""
# <tracecat_expression_dsl_reference>
# Tracecat's expression DSL is a powerful way to reference and manipulate data in your workflows and actions.

#   <overview>
#     Tracecat expressions allow dynamic data referencing and manipulation within workflows.
#     They are used in action inputs, run-if conditions, loop expressions, and output schemas.
#     This document provides a reference for the Tracecat Expression DSL. For the most current and exhaustive details, always refer to the official Tracecat documentation.
#   </overview>


#     Here's the complete Lark grammar defining the structure of a Tracecat Expression:
#     <lark_grammar>
#     {expressions_grammar}
#     </lark_grammar>

#   <general_syntax>
#     Expressions are wrapped using `${{{{ <expression_content> }}}}`.
#     Example: `${{{{ TRIGGER.data.id }}}}`
#   </general_syntax>

#   <contexts>
#     <context keyword="ACTIONS">
#       <name>ACTIONS Context</name>
#       <description>References outputs from previous actions in the same workflow. Actions are referenced by a sluggified version of their name.</description>
#       <syntax_pattern>ACTIONS.&lt;action_slug&gt;.result.&lt;jsonpath&gt;</syntax_pattern>
#       <jsonpath_note>JSONPath expressions (e.g., `$.data.field` or `data.field`) are used to navigate the JSON structure of action results. Standard JSONPath syntax applies.</jsonpath_note>
#       <example>
#         To get a 'temperature' field from an action named 'Get Weather':
#         `${{{{ ACTIONS.get_weather.result.data.current.temperature_2m }}}}`
#         We use the `jsonpath_ng` python library to parse the JSON path expressions.
#       </example>
#     </context>

#     <context keyword="TRIGGER">
#       <name>TRIGGER Context</name>
#       <description>References data passed to the workflow trigger. This can be from a webhook, a manual UI trigger, or the 'Execute Child Workflow' action. The trigger data is treated as a JSON object.</description>
#       <syntax_pattern>TRIGGER.&lt;jsonpath&gt;</syntax_pattern>
#       <example>
#         If a webhook sends `{{"user_id": 123, "details": {{"status": "active"}}}}`:
#         To get 'user_id': `${{{{ TRIGGER.user_id }}}}`
#         To get 'status': `${{{{ TRIGGER.details.status }}}}`
#       </example>
#     </context>

#     <context keyword="SECRETS">
#       <name>SECRETS Context</name>
#       <description>Accesses sensitive data stored in Tracecat's built-in secrets manager. Secrets are scoped to a workspace, encrypted at rest, and retrieved at runtime.</description>
#       <syntax_pattern>SECRETS.&lt;secret_name&gt;.&lt;secret_key&gt;</syntax_pattern>
#       <example>
#         To retrieve a secret named 'api_credentials' with a key 'token':
#         `${{{{ SECRETS.api_credentials.token }}}}`
#       </example>
#     </context>

#     <context keyword="FN">
#       <name>FN Context</name>
#       <description>Provides a set of inline functions for data manipulation, type conversion, and other utilities. For a full list, consult the Tracecat functions cheatsheet in the official documentation.</description>
#       <syntax_pattern>FN.&lt;function_name&gt;(&lt;arg1&gt;, &lt;arg2&gt;, ...)</syntax_pattern>
#       <function_categories>
#         <category name="JSON Processing">
#           <function_example name="deserialize_json" usage="FN.deserialize_json(string_to_parse)" description="Parse a JSON string into an object." />
#           <function_example name="serialize_json" usage="FN.serialize_json(object_to_serialize)" description="Convert an object to a JSON string." />
#           <function_example name="prettify_json" usage="FN.prettify_json(json_object_or_string)" description="Format JSON for readability." />
#           <function_example name="lookup" usage="FN.lookup(object, key, [default_value])" description="Safely access a potentially missing property in an object or dictionary." />
#           <function_example name="index_by_key" usage="FN.index_by_key(list_of_objects, key_name, [value_name])" description="Convert a list of objects into an object indexed by a given key. If value_name is provided, the new object's values will be the values of that key from the original objects." />
#           <function_example name="merge" usage="FN.merge(list_of_objects_or_dictionaries)" description="Merge multiple objects or dictionaries into one." />
#         </category>
#         <category name="Date/Time Processing">
#           <function_example name="to_datetime" usage="FN.to_datetime(iso_string_or_timestamp)" description="Convert an ISO 8601 string or a Unix timestamp to a datetime object." />
#           <function_example name="format_datetime" usage="FN.format_datetime(datetime_object_or_iso_string, format_string)" description="Format a datetime object or ISO string into a custom string format (e.g., '%Y-%m-%d %H:%M:%S')." />
#           <function_example name="to_timestamp" usage="FN.to_timestamp(datetime_object_or_iso_string)" description="Convert a datetime object or ISO string to a Unix timestamp (seconds since epoch)." />
#           <function_example name="hours_between" usage="FN.hours_between(datetime1, datetime2)" description="Calculate the difference in hours between two datetime objects or ISO strings." />
#         </category>
#         <category name="Text Processing">
#           <function_example name="regex_extract" usage="FN.regex_extract(pattern, text, [group_index_or_name])" description="Extract text using a regular expression. Optionally specify a capture group." />
#           <function_example name="uppercase" usage="FN.uppercase(text)" description="Convert text to uppercase." />
#           <function_example name="lowercase" usage="FN.lowercase(text)" description="Convert text to lowercase." />
#           <function_example name="join" usage="FN.join(list_of_strings, separator)" description="Join a list of strings with a separator." />
#           <function_example name="split" usage="FN.split(string, separator, [max_splits])" description="Split a string by a separator into a list of strings." />
#           <function_example name="trim" usage="FN.trim(text)" description="Removes leading and trailing whitespace from text." />
#           <function_example name="replace" usage="FN.replace(text, old_substring, new_substring, [count])" description="Replaces occurrences of a substring with another substring." />
#         </category>
#         <category name="IP Addresses">
#           <function_example name="check_ip_version" usage="FN.check_ip_version(ip_string)" description="Check if an IP address is IPv4 or IPv6. Returns 4 or 6, or None if invalid." />
#           <function_example name="ipv4_is_public" usage="FN.ipv4_is_public(ipv4_string)" description="Check if an IPv4 address is public." />
#           <function_example name="ipv4_is_private" usage="FN.ipv4_is_private(ipv4_string)" description="Check if an IPv4 address is private." />
#         </category>
#          <category name="Type Conversion Functions (distinct from typecasting syntax)">
#             <function_example name="to_int" usage="FN.to_int(value)" description="Converts a value to an integer using function syntax." />
#             <function_example name="to_float" usage="FN.to_float(value)" description="Converts a value to a float using function syntax." />
#             <function_example name="to_str" usage="FN.to_str(value)" description="Converts a value to a string using function syntax." />
#             <function_example name="to_bool" usage="FN.to_bool(value)" description="Converts a value to a boolean (handles 'true', 'false', 1, 0, etc.) using function syntax." />
#         </category>
#          <category name="List and Dictionary Operations">
#             <function_example name="length" usage="FN.length(list_or_string_or_dict)" description="Returns the length of a list, string, or number of keys in a dictionary." />
#             <function_example name="contains" usage="FN.contains(list_or_string_or_dict, item)" description="Checks if an item is present in a list, substring in a string, or key in a dictionary." />
#             <function_example name="keys" usage="FN.keys(dict)" description="Returns a list of keys from a dictionary." />
#             <function_example name="values" usage="FN.values(dict)" description="Returns a list of values from a dictionary." />
#             <function_example name="get_element" usage="FN.get_element(list, index, [default_value])" description="Safely get an element from a list by index." />
#         </category>
#         <category name="Mathematical Operations">
#             <function_example name="sum" usage="FN.sum(list_of_numbers)" description="Calculates the sum of a list of numbers." />
#             <function_example name="avg" usage="FN.avg(list_of_numbers)" description="Calculates the average of a list of numbers." />
#             <function_example name="min" usage="FN.min(list_of_numbers_or_strings)" description="Finds the minimum value in a list." />
#             <function_example name="max" usage="FN.max(list_of_numbers_or_strings)" description="Finds the maximum value in a list." />
#         </category>
#       </function_categories>
#     </context>
#   </contexts>

#   <operators>
#     <description>
#       Standard arithmetic (+, -, *, /, %) and logical (==, !=, &gt;, &lt;, &gt;=, &lt;=, &&, ||, in, not in, is, is not)
#       operators can be used on compatible data types (int, float, str, datetime, timedelta, list, dict).
#       Note: '&&' is for logical AND, '||' for logical OR.
#     </description>
#     <examples>
#       <example type="arithmetic_integer">`${{{{ 1 + 2 }}}}` results in `3`</example>
#       <example type="string_concatenation">`${{{{ "hello " + "world" }}}}` results in `"hello world"`</example>
#       <example type="logical">`${{{{ TRIGGER.count > 10 && TRIGGER.status == "active" }}}}`</example>
#       <example type="membership">`${{{{ "error" in TRIGGER.message_list }}}}`</example>
#     </examples>
#   </operators>

#   <typecasting>
#     <description>Data can be explicitly converted from one type to another using dedicated syntax.</description>
#     <syntax>
#       <inline_casting>Using a function-like syntax: `${{{{ <type_name>(<expression>) }}}}` (e.g., `${{{{ int("101") }}}}`)</inline_casting>
#       <trailing_casting>Using a trailing arrow syntax: `${{{{ <expression> -> <type_name> }}}}` (e.g., `${{{{ "101" -> int }}}}`)</trailing_casting>
#     </syntax>
#     <supported_types>
#       <type name="int" behavior="Converts to Python integer." />
#       <type name="float" behavior="Converts to Python float." />
#       <type name="str" behavior="Converts to Python string." />
#       <type name="bool" behavior="Converts to Python boolean. True for truthy values (e.g., non-empty strings/lists, non-zero numbers, case-insensitive 'true'). False otherwise (e.g., empty strings/lists, zero, case-insensitive 'false', None)." />
#     </supported_types>
#     <example>
#       `${{{{ "101" -> int }}}}` or `${{{{ int("101") }}}}` both result in the integer `101`.
#     </example>
#   </typecasting>

# </tracecat_expression_dsl_reference>

# <tracecat_expression_expectation_schema>
#   <description>
#     Within a Tracecat Workflow DSL, the 'entrypoint' section can define an 'expects' schema.
#     This schema specifies the expected structure and types for the data provided when the workflow is triggered.
#     It allows for validation of trigger inputs against a defined contract.
#   </description>
#   <structure>
#     The 'expects' schema is a dictionary where each key is a field name, and the value is an object defining:
#     - 'type': A string representing the data type (e.g., "int", "str", "list[str]", "dict[str,int]", "enum[\"value1\",\"value2\"]", "datetime").
#     - 'description' (optional): A string describing the field.
#     - 'default' (optional): A default value for the field if not provided in the trigger input.
#     This structure is defined by the 'ExpectedField' model. At runtime, Tracecat uses this schema to dynamically create a Pydantic model for validation.
#   </structure>
#   <example_usage_in_workflow_dsl>
#     ```yaml
#     entrypoint:
#       expects:
#         user_id:
#           type: "str"
#           description: "The unique identifier for the user."
#         event_type:
#           type: "enum[\"login\",\"logout\",\"purchase\"]"
#           description: "The type of event that occurred."
#         payload:
#           type: "dict[str,any]"
#           description: "The event payload."
#           default: null
#     ```
#   </example_usage_in_workflow_dsl>
#   Here's the full lark grammar for a Tracecat Expectation type:
#   <lark_grammar>
#   {expectations_grammar}
#   </lark_grammar>
# </tracecat_expression_expectation_schema>
#     """
#     )
#     return xml_reference.strip()


# @mcp.tool(
#     name="get_workflow_definition_schema",
#     description="Get the schema for the workflow definition DSL.",
# )
# async def get_workflow_definition_schema() -> str:
#     """Get the schema of the workflow definition."""
#     async with get_client() as client:
#         response = await client.get("/builder/resources/workflow-definition")
#     response.raise_for_status()
#     match raw_data := response.json():
#         case {
#             "id": "workflow-definition",
#             "content_type": "application/vnd.tc+json",
#             "data": dsl_schema,
#         }:
#             pass
#         case _:
#             raise ValueError(f"Unexpected content type: {raw_data}")

#     return textwrap.dedent(
#         f"""
#         Tracecat's workflow definition is a YAML document that describes a workflow.

#         The JsonSchema definition for the workflow definition is as follows:
#         <workflow_definition_schema>
#         {yaml.dump(dsl_schema)}
#         </workflow_definition_schema>
#         """
#     )


@mcp.tool(
    name="usage_guide",
    description="Get the usage guide for Tracecat.",
)
def get_usage_guide() -> str:
    """Get the usage guide for Tracecat."""
    # Read the md file
    path = Path(
        "/Users/daryllim/dev/org/tracecat/.llm/ai-builder/how_to_use_tracecat.md"
    )
    with path.open() as f:
        return f.read()


# --- Actions ---


@mcp.tool(
    name="list_actions_in_registry",
    description="List all actions in the registry. Optionally limit the number of actions returned.",
)
async def list_actions_in_registry() -> list[dict[str, Any]]:
    """
    List all registry actions.

    Returns:
        list[dict[str, Any]]: A list of registry actions.

    Raises:
        httpx.HTTPStatusError: If the HTTP request fails.
        ValueError: If the response is not a list or contains invalid items.
        TypeError: If limit is not a positive integer or None.
    """
    async with get_client() as client:
        response = await client.get("/registry/actions")
    response.raise_for_status()
    actions = response.json()
    if not isinstance(actions, list):
        raise ValueError("Expected a list of registry actions from the API response.")
    return actions


@mcp.tool(
    name="get_actions_details",
    description="Retrieve detailed information about a specific registry action.",
)
async def get_actions_details(
    action_ids: Annotated[
        list[ActionId],
        Field(
            description="A list of action IDs to retrieve details for.", min_length=1
        ),
    ],
) -> list[dict[str, Any]]:
    """
    Get detailed information for a specific action by its ID.

    Args:
        action_id: The unique identifier of the action.

    Returns:
        A dictionary containing detailed information about the action,
        such as full input/output schemas and configuration requirements.

    Raises:
        httpx.HTTPStatusError: If the HTTP request to the Tracecat API fails.
    """

    async with get_client() as client:
        coros = [
            client.get(f"/registry/actions/{action_id}") for action_id in action_ids
        ]
        responses = await asyncio.gather(*coros, return_exceptions=True)
    results = []
    for r in responses:
        if isinstance(r, httpx.Response):
            results.append(r.json())
        elif isinstance(r, Exception):
            results.append(str(r))
        else:
            raise ValueError(f"Unexpected response type: {type(r)}")
    return results


@mcp.tool(
    name="run_action_standalone",
    description=textwrap.dedent(
        """
        Execute a single Tracecat action in isolation with specified inputs.
        You can pass literal values or use expressions with ${{ ... }} syntax in the inputs.
        For example:
            run_action_standalone(action="my_action", inputs={"x": 5, "y": "${{ 2 + 2 }}"})
        This will evaluate the expression inside ${{ ... }} at runtime.
        The action will be executed and the result will be returned.
        """
    ),
)
async def run_action_standalone(
    action_id: ActionId,
    inputs: Annotated[
        dict[str, Any],
        Field(description="A dictionary of input arguments to the action."),
    ],
) -> dict[str, Any]:
    """
    Execute a single registry action in isolation.

    Args:
        action_id: The unique identifier of the action to run.
        action_inputs: A dictionary of inputs to provide to the action.

    Returns:
        A dictionary containing the output of the action and any execution status.

    Raises:
        ValueError: If action_id is invalid.
        httpx.HTTPStatusError: If the HTTP request to the Tracecat API fails.
    """
    if not action_id or not isinstance(action_id, str):
        raise ValueError("action must be a non-empty string.")

    # Set the workspace ID in the headers for all requests
    workspace = manager.get_workspace()
    if not workspace:
        raise ValueError(
            "Please use `tracecat workspace checkout` to select a workspace."
        )
    async with get_client() as client:
        # Assuming an endpoint like /registry/actions/{action_id}/run
        response = await client.post(
            "/builder/actions/run",
            params={"workspace_id": workspace.id},
            json={"action": action_id, "args": inputs},
            timeout=30,
        )
    response.raise_for_status()
    return response.json()


# --- Workflows ---


@mcp.tool(
    name="validate_workflow_definition",
    description="Validate a workflow definition.",
)
async def validate_workflow_definition(
    definition_path: Annotated[
        str,
        Field(
            description="The absolute path to the workflow definition yaml file. .yml or .yaml extension is required.",
            examples=["/Users/john/tracecat/workflows/my_workflow.yaml"],
        ),
    ],
) -> dict[str, Any]:
    """Validate a workflow definition."""
    path = validate_wf_defn_yaml_path(definition_path)
    dsl = DSLInput.from_yaml(path)
    async with get_client() as client:
        response = await client.post(
            "/builder/workflows/validate-definition",
            json={"dsl": dsl.model_dump()},
        )
    response.raise_for_status()
    return response.json()


@mcp.tool(
    name="execute_workflow",
    description="Execute a Tracecat workflow from its DSL and provide inputs.",
)
async def execute_workflow(
    dsl_path: Annotated[
        str,
        Field(description="The path to the workflow DSL YAML file."),
    ],
    trigger_inputs: Annotated[
        dict[str, Any] | None,
        Field(
            description="A single key-value pair of inputs=<JSON value> representing the inputs to the workflow's trigger.",
            examples=[
                {"inputs": {"x": 5, "y": 10}},
                {"inputs": {"x": 5, "y": "${{ 2 + 2 }}"}},
            ],
            min_length=1,
            max_length=1,
        ),
    ] = None,
) -> dict[str, Any]:
    """
    Execute a Tracecat workflow.

    The workflow can be provided as a path to a YAML file or as a dictionary.
    One of dsl_path or dsl_content must be provided.

    Args:
        dsl_path: Path to the workflow DSL YAML file.
        dsl_content: The workflow DSL as a dictionary.
        workflow_inputs: Inputs to the workflow's trigger.

    Returns:
        A dictionary containing the workflow execution result, including
        outputs, execution trace, and status.

    Raises:
        ValueError: If neither dsl_path nor dsl_content is provided,
                    or if dsl_path is invalid.
        httpx.HTTPStatusError: If the HTTP request to the Tracecat API fails.
    """
    match trigger_inputs:
        case {"inputs": inputs, **_rest}:
            wf_inputs = inputs
        case None:
            wf_inputs = None
        case _:
            raise ValueError(
                "trigger_inputs must be a single key-value pair of inputs=<JSON value>"
            )

    path = validate_wf_defn_yaml_path(dsl_path)
    dsl = DSLInput.from_yaml(path)
    async with get_client() as client:
        response = await client.post(
            "/builder/workflows/execute",
            json={"dsl": dsl.model_dump(), "trigger_inputs": wf_inputs},
        )
    response.raise_for_status()
    return response.json()


if __name__ == "__main__":
    # Initialize and run the server
    mcp.run(transport="stdio")
