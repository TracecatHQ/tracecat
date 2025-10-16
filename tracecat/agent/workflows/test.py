from dataclasses import replace

from pydantic_ai import Agent, RunContext, ToolDefinition
from pydantic_ai.models.test import TestModel
from pydantic_ai.toolsets import FunctionToolset

descriptions = {
    "temperature_celsius": "Get the temperature in degrees Celsius",
    "temperature_fahrenheit": "Get the temperature in degrees Fahrenheit",
    "weather_conditions": "Get the current weather conditions",
    "current_time": "Get the current time",
}


async def add_descriptions(
    ctx: RunContext, tool_defs: list[ToolDefinition]
) -> list[ToolDefinition] | None:
    return [
        replace(tool_def, description=description)
        if (description := descriptions.get(tool_def.name, None))
        else tool_def
        for tool_def in tool_defs
    ]


prepared_toolset = FunctionToolset().prepared(add_descriptions)

test_model = TestModel()
agent = Agent(test_model, toolsets=[prepared_toolset])
result = agent.run_sync("What tools are available?")
print(test_model.last_model_request_parameters)
"""
[
    ToolDefinition(
        name='temperature_celsius',
        parameters_json_schema={
            'additionalProperties': False,
            'properties': {'city': {'type': 'string'}},
            'required': ['city'],
            'type': 'object',
        },
        description='Get the temperature in degrees Celsius',
    ),
    ToolDefinition(
        name='temperature_fahrenheit',
        parameters_json_schema={
            'additionalProperties': False,
            'properties': {'city': {'type': 'string'}},
            'required': ['city'],
            'type': 'object',
        },
        description='Get the temperature in degrees Fahrenheit',
    ),
    ToolDefinition(
        name='weather_conditions',
        parameters_json_schema={
            'additionalProperties': False,
            'properties': {'city': {'type': 'string'}},
            'required': ['city'],
            'type': 'object',
        },
        description='Get the current weather conditions',
    ),
    ToolDefinition(
        name='current_time',
        parameters_json_schema={
            'additionalProperties': False,
            'properties': {},
            'type': 'object',
        },
        description='Get the current time',
    ),
]
"""
