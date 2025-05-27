# How to use Tracecat MCP tools

This document provides a reference for the Tracecat Expression DSL. For the most current and exhaustive details, always refer to the official Tracecat documentation.

## Expressions DSL

Tracecat's expression DSL is a powerful way to reference and manipulate data in your workflows and actions.

<overview>
Tracecat expressions allow dynamic data referencing and manipulation within workflows.
They are used in action inputs, run-if conditions, loop expressions, and output schemas.
This document provides a reference for the Tracecat Expression DSL. For the most current and exhaustive details, always refer to the official Tracecat documentation.
</overview>

Here's the complete Lark grammar defining the structure of a Tracecat Expression:
<expression_dsl_lark_grammar>
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

arg_list: (expression ("," expression)\*)?

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

PARTIAL*JSONPATH_EXPR: /(?:\.(\.)?(?:[a-zA-Z*][a-zA-Z0-9_]_|\*|'[^']_'|"[^"]_"|\[[^\]]+\]|\`[^\`]_\`)|\.\.|\[[^\]]+\])+/

OPERATOR: "not in" | "is not" | "in" | "is" | "==" | "!=" | ">=" | "<=" | ">" | "<" | "&&" | "||" | "+" | "-" | "_" | "/" | "%"
TYPE_SPECIFIER: "int" | "float" | "str" | "bool"
STRING_LITERAL: /'(?:[^'\\]|\\.)_'/ | /"(?:[^"\\]|\\.)\*"/
BOOL_LITERAL: "True" | "False"
NUMERIC_LITERAL: /\d+(\.\d+)?/
NONE_LITERAL: "None"

%import common.CNAME
%import common.INT -> NUMBER
%import common.WS
%ignore WS
</expression_dsl_lark_grammar>

<general_syntax>
Expressions are wrapped using `${{ <expression_content> }}`.
Example: `${{ TRIGGER.data.id }}`
</general_syntax>

<contexts>
Contexts are the leading namespaces for expressions.

<context namespace="ACTIONS">
    <name>ACTIONS Context</name>
    <description>
    References outputs from previous actions in the same workflow.
    Actions are referenced by a sluggified version of their name.
    Any action can reference any other action's result or error.
    </description>
    <syntax_pattern>
    ACTIONS.action_ref.[result|error].jsonpath.to.field
    </syntax_pattern>
    <jsonpath_note>JSONPath expressions (e.g., `$.data.field` or `data.field`) are used to navigate the JSON structure of action results. Standard JSONPath syntax applies.</jsonpath_note>
    <example>
    Suppose the output of an action named 'users' is:
    <example_object>
    ```json
    {
        "result": [
            {
                "name": "Alice",
                "age": 30,
                "gender": "female",
                "active": true,
                "contact": {
                    "email": "alice@example.com",
                    "phone": "123-456-7890"
                }
            },
            {
                "name": "Bob",
                "age": 40,
                "gender": "male",
                "active": false,
                "contact": {
                    "email": "bob@example.com",
                    "phone": "098-765-4321"
                }
            },
            {
                "name": "Charlie",
                "age": 50,
                "gender": "male",
                "active": true,
                "contact": {
                    "email": "charlie@example.com",
                    "phone": "111-222-3333"
                }
            }
        ],
        "empty": [],
        "null_value": null
    }
    ```
    </example_object>

    <example_usage>
        <description>To get the 'name' field from the first user:</description>
        <expression>${{ ACTIONS.users.result[0].name }}</expression>
        <result>Alice</result>
    </example_usage>
    <example_usage>
        <description>To get all user names:</description>
        <expression>${{ ACTIONS.users.result[*].name }}</expression>
        <result>["Alice", "Bob", "Charlie"]</result>
    </example_usage>
    <example_usage>
        <description>To filter users by age and get their emails:</description>
        <expression>${{ ACTIONS.users.result[?age >= 40].contact.email }}</expression>
        <result>["bob@example.com", "charlie@example.com"]</result>
    </example_usage>
    <example_usage>
        <description>To get the 'temperature' field from an action named 'Get Weather':</description>
        <expression>${{ ACTIONS.get_weather.result.data.current.temperature_2m }}</expression>
    </example_usage>
    <example_usage>
        <description>To substitute part of a string in a field using a function:</description>
        <expression>${{ ACTIONS.users.result[?gender == 'male'].contact.email.`sub(/example.com/, example.net)` }}</expression>
        <result>["bob@example.net", "charlie@example.net"]</result>
      </example_usage>
    <example_usage>
        <description>To access a deeply nested field:</description>
        <expression>${{ ACTIONS.some_action.result.data.nested.value }}</expression>
        <result>100</result>
    </example_usage>
    <example_usage>
        <description>To safely access a possibly missing property:</description>
        <expression>${{ FN.lookup(ACTIONS.some_action.result, "optional_field", "default_value") }}</expression>
        <result>default_value</result>
    </example_usage>
    <example_usage>
        <description>To get a value from an empty list (returns None):</description>
        <expression>${{ ACTIONS.empty[0].index }}</expression>
        <result>None</result>
    </example_usage>
    <example_usage>
        <description>To get a null value:</description>
        <expression>${{ ACTIONS.null_value.result.result }}</expression>
        <result>None</result>
    </example_usage>

    These are just a few examples. For a complete list of functions, view your available tools.
    </example>

</context>

<context namespace="TRIGGER">
    <name>TRIGGER Context</name>
    <description>References data passed to the workflow trigger. This can be from a webhook, a manual UI trigger, or the 'Execute Child Workflow' action. The trigger data is treated as a JSON object.</description>
    <syntax_pattern>
    TRIGGER.jsonpath.to.field
    </syntax_pattern>
    <example>
    If a webhook sends `{{"user_id": 123, "details": {{"status": "active"}}`:
    To get 'user_id': `${{ TRIGGER.user_id }}`
    To get 'status': `${{ TRIGGER.details.status }}`
    </example>
</context>

<context namespace="SECRETS">
    <name>SECRETS Context</name>
    <description>Accesses sensitive data stored in Tracecat's built-in secrets manager. Secrets are scoped to a workspace, encrypted at rest, and retrieved at runtime.</description>
    <syntax_pattern>
    SECRETS.secret_name.secret_key
    </syntax_pattern>
    <example>
    To retrieve a secret named 'api_credentials' with a key 'token':
    `${{ SECRETS.api_credentials.token }}`
    </example>
</context>

<context namespace="FN">
  <name>FN Context</name>
  <description>Provides a set of inline functions for data manipulation, type conversion, and other utilities. For a full list, consult the Tracecat functions cheatsheet in the official documentation.</description>
  <syntax_pattern>FN.function_name(arg1, arg2, ...args)</syntax_pattern>
  <function_categories>
  <category name="JSON Processing">
      <function_example name="deserialize_json" usage="FN.deserialize_json(string_to_parse)" description="Parse a JSON string into an object." />
      <function_example name="serialize_json" usage="FN.serialize_json(object_to_serialize)" description="Convert an object to a JSON string." />
      <function_example name="prettify_json" usage="FN.prettify_json(json_object_or_string)" description="Format JSON for readability." />
      <function_example name="lookup" usage="FN.lookup(object, key, [default_value])" description="Safely access a potentially missing property in an object or dictionary." />
      <function_example name="index_by_key" usage="FN.index_by_key(list_of_objects, key_name, [value_name])" description="Convert a list of objects into an object indexed by a given key. If value_name is provided, the new object's values will be the values of that key from the original objects." />
      <function_example name="merge" usage="FN.merge(list_of_objects_or_dictionaries)" description="Merge multiple objects or dictionaries into one." />
  </category>
  <category name="Date/Time Processing">
      <function_example name="to_datetime" usage="FN.to_datetime(iso_string_or_timestamp)" description="Convert an ISO 8601 string or a Unix timestamp to a datetime object." />
      <function_example name="format_datetime" usage="FN.format_datetime(datetime_object_or_iso_string, format_string)" description="Format a datetime object or ISO string into a custom string format (e.g., '%Y-%m-%d %H:%M:%S')." />
      <function_example name="to_timestamp" usage="FN.to_timestamp(datetime_object_or_iso_string)" description="Convert a datetime object or ISO string to a Unix timestamp (seconds since epoch)." />
      <function_example name="hours_between" usage="FN.hours_between(datetime1, datetime2)" description="Calculate the difference in hours between two datetime objects or ISO strings." />
  </category>
  <category name="Text Processing">
      <function_example name="regex_extract" usage="FN.regex_extract(pattern, text, [group_index_or_name])" description="Extract text using a regular expression. Optionally specify a capture group." />
      <function_example name="uppercase" usage="FN.uppercase(text)" description="Convert text to uppercase." />
      <function_example name="lowercase" usage="FN.lowercase(text)" description="Convert text to lowercase." />
      <function_example name="join" usage="FN.join(list_of_strings, separator)" description="Join a list of strings with a separator." />
      <function_example name="split" usage="FN.split(string, separator, [max_splits])" description="Split a string by a separator into a list of strings." />
      <function_example name="trim" usage="FN.trim(text)" description="Removes leading and trailing whitespace from text." />
      <function_example name="replace" usage="FN.replace(text, old_substring, new_substring, [count])" description="Replaces occurrences of a substring with another substring." />
  </category>
  <category name="IP Addresses">
      <function_example name="check_ip_version" usage="FN.check_ip_version(ip_string)" description="Check if an IP address is IPv4 or IPv6. Returns 4 or 6, or None if invalid." />
      <function_example name="ipv4_is_public" usage="FN.ipv4_is_public(ipv4_string)" description="Check if an IPv4 address is public." />
      <function_example name="ipv4_is_private" usage="FN.ipv4_is_private(ipv4_string)" description="Check if an IPv4 address is private." />
  </category>
      <category name="Type Conversion Functions (distinct from typecasting syntax)">
      <function_example name="to_int" usage="FN.to_int(value)" description="Converts a value to an integer using function syntax." />
      <function_example name="to_float" usage="FN.to_float(value)" description="Converts a value to a float using function syntax." />
      <function_example name="to_str" usage="FN.to_str(value)" description="Converts a value to a string using function syntax." />
      <function_example name="to_bool" usage="FN.to_bool(value)" description="Converts a value to a boolean (handles 'true', 'false', 1, 0, etc.) using function syntax." />
  </category>
      <category name="List and Dictionary Operations">
      <function_example name="length" usage="FN.length(list_or_string_or_dict)" description="Returns the length of a list, string, or number of keys in a dictionary." />
      <function_example name="contains" usage="FN.contains(list_or_string_or_dict, item)" description="Checks if an item is present in a list, substring in a string, or key in a dictionary." />
      <function_example name="keys" usage="FN.keys(dict)" description="Returns a list of keys from a dictionary." />
      <function_example name="values" usage="FN.values(dict)" description="Returns a list of values from a dictionary." />
      <function_example name="get_element" usage="FN.get_element(list, index, [default_value])" description="Safely get an element from a list by index." />
  </category>
  <category name="Mathematical Operations">
      <function_example name="sum" usage="FN.sum(list_of_numbers)" description="Calculates the sum of a list of numbers." />
      <function_example name="avg" usage="FN.avg(list_of_numbers)" description="Calculates the average of a list of numbers." />
      <function_example name="min" usage="FN.min(list_of_numbers_or_strings)" description="Finds the minimum value in a list." />
      <function_example name="max" usage="FN.max(list_of_numbers_or_strings)" description="Finds the maximum value in a list." />
  </category>
  </function_categories>
</context>
</contexts>

<operators>
Standard arithmetic (+, -, *, /, %) and logical (==, !=, >, <;, >=, <=, &&, ||, in, not in, is, is not)
operators can be used on compatible data types (int, float, str, datetime, timedelta, list, dict).
Note: '&&' is for logical AND, '||' for logical OR.
<examples>
    <example type="arithmetic_integer">`${{ 1 + 2 }}` results in `3`</example>
    <example type="string_concatenation">`${{ "hello " + "world" }}` results in `"hello world"`</example>
    <example type="logical">`${{ TRIGGER.count > 10 && TRIGGER.status == "active" }}`</example>
    <example type="membership">`${{ "error" in TRIGGER.message_list }}`</example>
</examples>
</operators>

  <typecasting>
    <description>Data can be explicitly converted from one type to another using dedicated syntax.</description>
    <syntax>
      <inline_casting>Using a function-like syntax: `${{ <type_name>(<expression>) }}` (e.g., `${{ int("101") }}`)</inline_casting>
      <trailing_casting>Using a trailing arrow syntax: `${{ <expression> -> <type_name> }}` (e.g., `${{ "101" -> int }}`)</trailing_casting>
    </syntax>
    <supported_types>
      <type name="int" behavior="Converts to Python integer." />
      <type name="float" behavior="Converts to Python float." />
      <type name="str" behavior="Converts to Python string." />
      <type name="bool" behavior="Converts to Python boolean. True for truthy values (e.g., non-empty strings/lists, non-zero numbers, case-insensitive 'true'). False otherwise (e.g., empty strings/lists, zero, case-insensitive 'false', None)." />
    </supported_types>
    <example>
      `${{ "101" -> int }}` or `${{ int("101") }}` both result in the integer `101`.
    </example>
  </typecasting>

</tracecat_expression_dsl_reference>

<tracecat_expression_expectation_schema>
<description>
Within a Tracecat Workflow DSL, the 'entrypoint' section can define an 'expects' schema.
This schema specifies the expected structure and types for the data provided when the workflow is triggered.
It allows for validation of trigger inputs against a defined contract.
</description>
<structure>
The 'expects' schema is a dictionary where each key is a field name, and the value is an object defining: - 'type': A string representing the data type (e.g., "int", "str", "list[str]", "dict[str,int]", "enum[\"value1\",\"value2\"]", "datetime"). - 'description' (optional): A string describing the field. - 'default' (optional): A default value for the field if not provided in the trigger input.
This structure is defined by the 'ExpectedField' model. At runtime, Tracecat uses this schema to dynamically create a Pydantic model for validation.
</structure>
<example_usage_in_workflow_dsl>

```yaml
entrypoint:
  expects:
    user_id:
          type: str
          description: The unique identifier for the user.
        event_type:
          type: enum["login","logout","purchase"]
          description: The type of event that occurred.
        payload:
          type: dict[str,any]
          description: The event payload.
          default: null
```

</example_usage_in_workflow_dsl>
Here's the full lark grammar for a Tracecat Expectation type:
<lark_grammar>
{expectations_grammar}
</lark_grammar>

## Control Flow

Tracecat workflows support several control flow mechanisms to manage the execution of actions.

### If-conditions

Every action can have an `If condition`. This allows you to specify a condition that determines whether the action should execute. The condition is an expression that evaluates to a boolean.

**Syntax:**
The `Run if` input for an action takes an expression:

```
${{ <expression_evaluating_to_boolean> }}
```

**Example:**
To run an action only if a previous action named `scan_url` was successful:

```
${{ ACTIONS.scan_url.result.data.message == "Submission successful" }}
```

**Common Conditional Patterns:**

- **Boolean Checks:**
  - `${{ bool(ACTIONS.is_enabled.result) }}` (True if `is_enabled.result` is truthy)
  - `${{ ACTIONS.is_enabled.result }}` (If `is_enabled.result` is already a boolean or truthy/falsy)
- **Basic Comparison:**
  - `${{ ACTIONS.user_role.result == "admin" }}`
  - `${{ ACTIONS.failed_attempts.result > 5 }}`
- **List Operations:**
  - `${{ ACTIONS.ip_address.result in ['192.168.1.1', '10.0.0.1'] }}`
  - `${{ ACTIONS.status.result not in ['error', 'failed', 'timeout'] }}`
- **Identity Checks:**
  - `${{ ACTIONS.optional_field.result == None }}` (Checks for null/None)
  - `${{ ACTIONS.required_field.result != None }}`
- **Combined Conditions (using `&&` for AND, `||` for OR):**
  - `${{ ACTIONS.user_role.result == "admin" && ACTIONS.cpu_usage.result >= 90 }}`
  - `${{ ACTIONS.memory_usage.result >= 95 || ACTIONS.cpu_usage.result >= 95 }}`

### Any / All Conditions (Join Strategy)

When multiple actions feed into a single downstream "joining" action, you can control its execution based on the outcomes of these upstream actions.
The `join_strategy` option on the joining node (found in its `If condition / Loops` tab) can be set to:

- `all`: The joining action runs only if all upstream actions connected to it complete successfully (or meet their own if-conditions).
- `any`: The joining action runs if at least one of the upstream actions connected to it completes successfully.

### Loops

Actions can be configured to run multiple times by iterating over a list of items. This is done using a loop expression in the `If condition / Loops` tab.

**Defining a Loop:**
Use the syntax `${{ for var.some_variable_name in some_list }}`.

- `var.some_variable_name`: A temporary variable name to hold each item from the list during iteration.
- `some_list`: An expression that resolves to a list (e.g., `TRIGGER.items`, `ACTIONS.another_action.result.data_list`).

**Example:**
To iterate over a list of numbers provided in the trigger data:

```
${{ for var.number in TRIGGER.numbers }}
```

**Using the Loop Variable:**
Inside the action's inputs, you can reference the current item from the loop using the variable name defined in the loop expression (e.g., `${{ var.number }}`).

**Example:**
If looping with `${{ for var.number in TRIGGER.numbers }}`, an action input could be:

```
value: ${{ var.number + 1 }}
```

If `TRIGGER.numbers` is `[1, 2, 3]`, the action would run three times. In the first run, `var.number` would be `1`, then `2`, then `3`. The output for this example would be `[2, 3, 4]` if the action adds 1 to the input and collects results.

### Action Dependencies

Actions can explicitly declare dependencies on other actions using the `depends_on` field. This tells the workflow engine that an action should only run after its dependencies have completed.

**Syntax:**
In the workflow DSL, you can specify dependencies in the action definition:

```yaml
actions:
  - ref: first_action
    action: core.transform.reshape
    args:
      # ... arguments for first_action

  - ref: second_action
    action: core.transform.reshape
    depends_on:
      - first_action
    args:
      # ... arguments for second_action

  - ref: third_action
    action: core.transform.reshape
    depends_on:
      - first_action
      - second_action
    args:
      # ... arguments for third_action
```

**Best Practices:**

1. **Prefer Linear Workflows**: When possible, design workflows as linear sequences of actions. This makes the flow easier to understand, debug, and maintain.

   ```yaml
   # Linear workflow (recommended)
   - ref: step_1
     action: core.transform.reshape

   - ref: step_2
     action: core.http.request
     depends_on:
       - step_1

   - ref: step_3
     action: core.transform.reshape
     depends_on:
       - step_2
   ```

2. **Limit Complex Dependencies**: While Tracecat supports complex dependency graphs, they can be harder to reason about. Use complex dependencies only when necessary for parallel processing.

3. **Dependencies vs. Data Flow**: Remember that `depends_on` defines execution order, not data flow. To use data from a previous action, you'll still need to reference it using `ACTIONS.<action_ref>.result`.

4. **Visualize Your Workflows**: Complex dependency structures are easier to understand when visualized. Use the Tracecat UI to view the workflow graph and ensure it matches your intended logic.

## Workflow definition

The JSON Schema definition for workflow definitions is as follows:

<workflow_definition_json_schema type="application/yaml">

```yaml
$defs:
  ActionRetryPolicy:
    properties:
      max_attempts:
        default: 1
        description:
          Total number of execution attempts. 0 means unlimited, 1 means
          no retries.
        title: Max Attempts
        type: integer
      retry_until:
        anyOf:
          - type: string
          - type: "null"
        default: null
        description: Retry until a specific condition is met.
        title: Retry Until
      timeout:
        default: 300
        description: Timeout for the action in seconds.
        title: Timeout
        type: integer
    title: ActionRetryPolicy
    type: object
  ActionStatement:
    properties:
      action:
        description: Action type. Equivalent to the UDF key.
        pattern: ^[a-z0-9_.]+$
        title: Action
        type: string
      args:
        description: Arguments for the action
        title: Args
        type: object
      depends_on:
        description: Task dependencies
        items:
          type: string
        title: Depends On
        type: array
      description:
        default: ""
        title: Description
        type: string
      for_each:
        anyOf:
          - type: string
          - items:
              type: string
            type: array
          - type: "null"
        default: null
        description: Iterate over a list of items and run the task for each item.
        title: For Each
      id:
        anyOf:
          - type: string
          - type: "null"
        default: null
        description:
          The action ID. If this is populated means there is a corresponding
          actionin the database `Action` table.
        title: Id
      interaction:
        anyOf:
          - description: An interaction configuration
            discriminator:
              mapping:
                approval: "#/$defs/ApprovalInteraction"
                response: "#/$defs/ResponseInteraction"
              propertyName: type
            oneOf:
              - $ref: "#/$defs/ResponseInteraction"
              - $ref: "#/$defs/ApprovalInteraction"
          - type: "null"
        default: null
        description: Whether the action is interactive.
        title: Interaction
      join_strategy:
        $ref: "#/$defs/JoinStrategy"
        default: all
        description:
          The strategy to use when joining on this task. By default, all
          branches must complete successfully before the join task can complete.
      ref:
        description: Unique reference for the task
        pattern: ^[a-z0-9_]+$
        title: Ref
        type: string
      retry_policy:
        $ref: "#/$defs/ActionRetryPolicy"
        description: Retry policy for the action.
      run_if:
        anyOf:
          - type: string
          - type: "null"
        default: null
        description: Condition to run the task
        title: Run If
      start_delay:
        default: 0.0
        description:
          Delay before starting the action in seconds. If `wait_until`
          is also provided, the `wait_until` timer will take precedence.
        title: Start Delay
        type: number
      wait_until:
        anyOf:
          - type: string
          - type: "null"
        default: null
        description:
          Wait until a specific date and time before starting. Overrides
          `start_delay` if both are provided.
        title: Wait Until
    required:
      - ref
      - action
    title: ActionStatement
    type: object
  ApprovalInteraction:
    description: Configuration for an approval interaction.
    properties:
      approve_if:
        anyOf:
          - type: string
          - type: "null"
        default: null
        description: Condition to approve the action.
        title: Approve If
      approver_groups:
        description: List of groups that are allowed to approve this action.
        items:
          type: string
        title: Approver Groups
        type: array
      message:
        default: ""
        description: Custom message to display to approvers.
        title: Message
        type: string
      required_approvers:
        default: 1
        description: Number of approvers required before the action can proceed.
        title: Required Approvers
        type: integer
      timeout:
        anyOf:
          - type: number
          - type: "null"
        default: null
        description: The timeout for the interaction in seconds.
        title: Timeout
      type:
        const: approval
        title: Type
        type: string
    required:
      - type
    title: ApprovalInteraction
    type: object
  DSLConfig:
    description: "This is the runtime configuration for the workflow.


      Activities don't need access to this."
    properties:
      environment:
        default: default
        description:
          The workflow's target execution environment. This is used to
          isolate secrets across different environments.If not provided, the default
          environment (default) is used.
        title: Environment
        type: string
      scheduler:
        default: dynamic
        description: The type of scheduler to use.
        enum:
          - static
          - dynamic
        title: Scheduler
        type: string
      timeout:
        default: 300
        description: The maximum number of seconds to wait for the workflow to complete.
        title: Timeout
        type: number
    title: DSLConfig
    type: object
  DSLEntrypoint:
    properties:
      expects:
        anyOf:
          - additionalProperties:
              $ref: "#/$defs/ExpectedField"
            type: object
          - type: "null"
        default: null
        description:
          Expected trigger input schema. Use this to specify the expected
          shape of the trigger input.
        title: Expects
      ref:
        anyOf:
          - type: string
          - type: "null"
        default: null
        description: The entrypoint action ref
        title: Ref
    title: DSLEntrypoint
    type: object
  ExpectedField:
    properties:
      default:
        anyOf:
          - {}
          - type: "null"
        default: null
        title: Default
      description:
        anyOf:
          - type: string
          - type: "null"
        default: null
        title: Description
      type:
        title: Type
        type: string
    required:
      - type
    title: ExpectedField
    type: object
  JoinStrategy:
    enum:
      - any
      - all
    title: JoinStrategy
    type: string
  ResponseInteraction:
    description: Configuration for a response interaction.
    properties:
      timeout:
        anyOf:
          - type: number
          - type: "null"
        default: null
        description: The timeout for the interaction in seconds.
        title: Timeout
      type:
        const: response
        title: Type
        type: string
    required:
      - type
    title: ResponseInteraction
    type: object
  Trigger:
    properties:
      args:
        title: Args
        type: object
      ref:
        pattern: ^[a-z0-9_]+$
        title: Ref
        type: string
      type:
        enum:
          - schedule
          - webhook
        title: Type
        type: string
    required:
      - type
      - ref
    title: Trigger
    type: object
description: "DSL definition for a workflow.


  The difference between this and a normal workflow engine is that here,

  our workflow execution order is defined by the DSL itself, independent

  of a workflow scheduler.


  With a traditional

  This allows the execution of the workflow to be fully deterministic."
properties:
  actions:
    items:
      $ref: "#/$defs/ActionStatement"
    title: Actions
    type: array
  config:
    $ref: "#/$defs/DSLConfig"
  description:
    title: Description
    type: string
  entrypoint:
    $ref: "#/$defs/DSLEntrypoint"
  error_handler:
    anyOf:
      - type: string
      - type: "null"
    default: null
    description: The action ref to handle errors.
    title: Error Handler
  inputs:
    description: Static input parameters
    title: Inputs
    type: object
  returns:
    anyOf:
      - {}
      - type: "null"
    default: null
    description: The action ref or value to return.
    title: Returns
  title:
    title: Title
    type: string
  triggers:
    items:
      $ref: "#/$defs/Trigger"
    title: Triggers
    type: array
required:
  - title
  - description
  - entrypoint
  - actions
title: DSLInput
type: object
```

</workflow_definition_json_schema>

## Best Practices

### Building workflows

- Test actions in isolation, then build up to more complex workflows.

### Data Transformations

- If data transformations are required, opt for `core.transform.reshape` with an object of expressions.
