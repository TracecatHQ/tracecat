from enum import StrEnum, auto


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


class TaskMarker(StrEnum):
    SKIP = auto()
    TERMINATED = auto()
