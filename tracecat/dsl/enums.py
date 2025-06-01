from enum import StrEnum, auto


class PlatformAction(StrEnum):
    CHILD_WORKFLOW_EXECUTE = "core.workflow.execute"
    TRANSFORM_EXPLODE = "core.transform.explode"
    TRANSFORM_IMPLODE = "core.transform.implode"


class FailStrategy(StrEnum):
    ISOLATED = "isolated"
    ALL = "all"


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
    """Sentinel values for implode operations."""

    IMPLODE_UNSET = "<|IMPLODE_UNSET|>"
