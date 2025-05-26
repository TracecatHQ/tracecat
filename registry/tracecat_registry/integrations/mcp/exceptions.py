import json
import dataclasses


class AgentRunError(RuntimeError):
    """Error raised when the agent run fails."""

    def __init__(
        self,
        exc_cls: type[Exception],
        exc_msg: str,
        message_history: list[dict] | None = None,
        deps: object | None = None,
    ):
        self.exc_cls = exc_cls
        self.exc_msg = exc_msg
        self.message_history = message_history
        self.deps = deps

        # Build comprehensive error message with debug info
        msg_parts = [f"Agent run failed with unhandled error: {exc_cls.__name__}"]

        if exc_msg:
            msg_parts.extend(
                [
                    "",
                    "Error details:",
                    f"  Type: {exc_cls.__name__}",
                    f"  Message: {exc_msg}",
                ]
            )

        if deps is not None:
            deps_info = [
                "",
                "Dependencies info:",
                f"  Type: {type(deps).__name__}",
            ]

            # Show dataclass fields if it's a dataclass
            if dataclasses.is_dataclass(deps):
                fields = dataclasses.fields(deps)
                field_values = []
                for field in fields:
                    try:
                        value = getattr(deps, field.name)
                        field_values.append(f"{field.name}={repr(value)}")
                    except AttributeError:
                        field_values.append(f"{field.name}=<missing>")
                deps_info.append(f"  Fields: {', '.join(field_values)}")

            msg_parts.extend(deps_info)

        if message_history:
            msg_parts.extend(
                ["", "Agent message history:", json.dumps(message_history, indent=2)]
            )

        super().__init__("\n".join(msg_parts))
