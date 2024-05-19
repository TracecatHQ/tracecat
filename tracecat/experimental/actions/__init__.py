"""Actions module for tracecat.

WARNING!!!
----------
Do not add `from __future__ import annotations` to any action module. This will cause class types to be resolved as strings."""

# Bring all actions into the namespace to be registered
from tracecat.experimental.actions.core import (
    cases,
    condition,
    email,
    example,
    http,
    llm,
)
from tracecat.experimental.actions.integrations import (
    aws_cloudtrail,
    datadog,
    emailrep,
    example_integration,
    project_discovery,
    sublime,
    urlscan,
    virustotal,
)

__all__ = [
    # Core
    "example",
    "http",
    "email",
    "llm",
    "condition",
    "cases",
    # Integrations
    "example_integration",
    "datadog",
    "aws_cloudtrail",
    "sublime",
    "urlscan",
    "project_discovery",
    "emailrep",
    "virustotal",
]
