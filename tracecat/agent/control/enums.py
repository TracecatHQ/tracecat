from enum import StrEnum


class CommandType(StrEnum):
    """What a session command asks the backend to do."""

    SUBMIT = "submit"
    STEER = "steer"
    DECIDE = "decide"
    ABORT = "abort"
    WITHDRAW = "withdraw"


class CommandState(StrEnum):
    """Where a session command is in its lifecycle. A state never moves back."""

    # Admitted but not yet bound to a turn, such as a queued follow-up.
    QUEUED = "queued"
    # Admitted for a turn.
    ACCEPTED = "accepted"
    # The backend applied the command's effect.
    APPLIED = "applied"
    # Terminal: the backend refused the command.
    REJECTED = "rejected"
    # Terminal: the user took the command back before the backend applied it.
    WITHDRAWN = "withdrawn"
    # Terminal: the command's target is finished.
    SETTLED = "settled"
