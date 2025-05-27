"""Models for tools.

This module will contain the models for function calling.
Use pydantic models here with `model_json_schema` to get the JSON schema for the tool.


"""

from pydantic import BaseModel


class CodebaseSearch(BaseModel):
    """Search the codebase for the given query."""

    query: str
    target_directories: list[str]
