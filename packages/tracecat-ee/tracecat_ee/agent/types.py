from __future__ import annotations

import uuid
from typing import ClassVar

from pydantic_core import core_schema


class AgentWorkflowID(str):
    """
    Custom string type for agent workflow IDs with pattern validation.

    This class uses Pydantic's __get_pydantic_core_schema__ to provide runtime
    validation when used in Pydantic models, ensuring the ID matches the expected
    pattern: 'agent/<uuid>'.
    """

    _prefix: ClassVar[str] = "agent/"
    _session_id: uuid.UUID

    @classmethod
    def __get_pydantic_core_schema__(
        cls,
        source_type: type,
        handler,
    ):
        python_schema = core_schema.no_info_plain_validator_function(cls._validate)

        return core_schema.json_or_python_schema(
            json_schema=core_schema.str_schema(
                pattern=rf"^{cls._prefix}[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$"
            ),
            python_schema=python_schema,
            serialization=core_schema.plain_serializer_function_ser_schema(
                lambda x: str(x)
            ),
        )

    @classmethod
    def _validate(cls, value):
        if isinstance(value, cls):
            return value
        if isinstance(value, str):
            # Validate the format
            if not value.startswith(cls._prefix):
                raise ValueError(f"Agent workflow ID must start with '{cls._prefix}'")
            try:
                uuid.UUID(value.removeprefix(cls._prefix))
            except ValueError as e:
                raise ValueError(f"Invalid UUID in agent workflow ID: {e}") from e
            # Use from_workflow_id to properly construct the instance
            return cls.from_workflow_id(value)
        raise TypeError(f"Expected str or AgentWorkflowID, got {type(value)}")

    def __new__(
        cls,
        session_id: uuid.UUID,
    ) -> AgentWorkflowID:
        obj = super().__new__(cls, f"{cls._prefix}{str(session_id)}")
        obj._session_id = session_id
        return obj

    @property
    def session_id(self) -> uuid.UUID:
        return self._session_id

    @classmethod
    def from_workflow_id(cls, agent_workflow_id: str) -> AgentWorkflowID:
        assert agent_workflow_id.startswith(cls._prefix), (
            f"Agent workflow ID must start with '{cls._prefix}'"
        )
        return cls(uuid.UUID(agent_workflow_id.removeprefix(cls._prefix)))

    @classmethod
    def extract_id(cls, key: str) -> uuid.UUID:
        assert key.startswith(cls._prefix), (
            f"Agent session key must start with '{cls._prefix}'"
        )
        return uuid.UUID(key.removeprefix(cls._prefix))
