import json


class AgentRunError(RuntimeError):
    """Error raised when the agent run fails."""

    def __init__(
        self,
        exc_cls: type[Exception],
        exc_msg: str,
        message_history: list[dict] | None = None,
    ):
        self.exc_cls = exc_cls
        self.exc_msg = exc_msg
        self.message_history = message_history

        # Build comprehensive error message with debug info
        msg_parts = [f"Agent run failed with unhandled error: {exc_cls.__name__}"]

        if exc_msg or message_history:
            msg_parts.append("")  # Empty line before details

        if exc_msg:
            msg_parts.extend(
                [
                    "Error details:",
                    f"  Type: {exc_cls.__name__}",
                    f"  Message: {exc_msg}",
                ]
            )

        if message_history:
            if exc_msg:
                msg_parts.append("")  # Space between sections
            msg_parts.extend(
                ["Agent message history:", json.dumps(message_history, indent=2)]
            )

        super().__init__("\n".join(msg_parts))
