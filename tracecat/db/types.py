from typing import Protocol


class PostgresConstraintError(Protocol):
    constraint_name: str | None
