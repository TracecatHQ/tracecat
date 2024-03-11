from typing import Literal

ActionType = Literal[
    "webhook",
    "http_request",
    "data_transform",
    "condition.compare",
    "condition.regex",
    "condition.membership",
    "llm.extract",
    "llm.label",
    "llm.translate",
    "llm.choice",
    "llm.summarize",
    "send_email",
    "receive_email",
    "open_case",
]
