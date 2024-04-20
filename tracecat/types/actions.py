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
    "integrations.example.secretive_add",
    "integrations.example.subtract",
    "integrations.example.complex_example",
    "integrations.example.join",
    ## Material Security
    "integrations.sublime_security.test",
    ## Datadog
    "integrations.datadog.list_security_signals",
    "integrations.datadog.update_security_signal_state",
    "integrations.datadog.list_detection_rules",
]
