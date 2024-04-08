from typing import Literal

ActionType = Literal[
    # Core primitives
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
    # Integrations
    "integrations.example.add",
    "integrations.example.subtract",
    "integrations.example.complex_example",
    ## Material Security
    "integrations.material_security.test",
    ## Datadog
    "integrations.datadog.test",
]
