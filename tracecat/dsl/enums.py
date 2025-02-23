from enum import StrEnum, auto


class CoreActions(StrEnum):
    CHILD_WORKFLOW_EXECUTE = "core.workflow.execute"
    TABLE_LOOKUP = "core.table.lookup"
    TABLE_INSERT_ROW = "core.table.insert_row"


class FailStrategy(StrEnum):
    ISOLATED = "isolated"
    ALL = "all"


class LoopStrategy(StrEnum):
    PARALLEL = "parallel"
    BATCH = "batch"
    SEQUENTIAL = "sequential"


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
