from enum import StrEnum, auto


class PlatformAction(StrEnum):
    CHILD_WORKFLOW_EXECUTE = "core.workflow.execute"
    TRANSFORM_SCATTER = "core.transform.scatter"
    TRANSFORM_GATHER = "core.transform.gather"
    LOOP_START = "core.loop.start"
    LOOP_END = "core.loop.end"
    AI_AGENT = "ai.agent"
    AI_PRESET_AGENT = "ai.preset_agent"
    AI_ACTION = "ai.action"
    RUN_PYTHON = "core.script.run_python"

    @classmethod
    def is_streamable(cls, action: str) -> bool:
        return action in (
            cls.AI_AGENT,
            cls.AI_PRESET_AGENT,
            cls.AI_ACTION,
        )

    @classmethod
    def interface_actions(cls) -> frozenset[str]:
        return frozenset(
            (
                cls.CHILD_WORKFLOW_EXECUTE,
                cls.TRANSFORM_SCATTER,
                cls.TRANSFORM_GATHER,
                cls.LOOP_START,
                cls.LOOP_END,
                cls.AI_AGENT,
                cls.AI_PRESET_AGENT,
                cls.AI_ACTION,
                cls.RUN_PYTHON,
            )
        )

    @classmethod
    def is_interface(cls, action: str) -> bool:
        return action in cls.interface_actions()

    @classmethod
    def is_template_step_supported(cls, action: str) -> bool:
        """Return whether a platform action can be used in template steps.

        Most platform/interface actions are orchestrated by DSLWorkflow and are not safe
        to embed within template actions. `core.script.run_python` is the exception
        because executor backends handle it as a concrete runtime action.
        """
        if not cls.is_interface(action):
            return True
        return action == cls.RUN_PYTHON


class FailStrategy(StrEnum):
    ISOLATED = "isolated"
    """If any fails, only the failed one will fail."""
    ALL = "all"
    """If any fails, all will fail."""


class LoopStrategy(StrEnum):
    PARALLEL = "parallel"
    BATCH = "batch"
    SEQUENTIAL = "sequential"


class WaitStrategy(StrEnum):
    WAIT = "wait"
    """
    In `wait` mode, this action will wait for all child workflows to complete before returning.
    Any child workflow failures will be reported as an error.
    """
    DETACH = "detach"
    """
    In `detach` mode, this action will return immediately after the child workflows are created.
    A fialing child workflow will not affect the parent.
    """


class SkipStrategy(StrEnum):
    ISOLATE = auto()
    PROPAGATE = auto()


class JoinStrategy(StrEnum):
    ANY = "any"
    ALL = "all"


class EdgeMarker(StrEnum):
    PENDING = "pending"
    VISITED = "visited"
    SKIPPED = "skipped"


class EdgeType(StrEnum):
    SUCCESS = "success"
    ERROR = "error"


class Sentinel(StrEnum):
    """Sentinel values for gather operations."""

    GATHER_UNSET = "__SENTINEL_GATHER_UNSET__"


class StreamErrorHandlingStrategy(StrEnum):
    PARTITION = "partition"
    """Partition the error into a list of errors."""
    INCLUDE = "include"
    """Include the error in the result."""
    DROP = "drop"
    """Drop the error."""
    RAISE = "raise"
    """Raise an error if any gathered item failed."""
