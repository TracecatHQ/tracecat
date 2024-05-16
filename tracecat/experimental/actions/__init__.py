"""Actions module for tracecat.

WARNING!!!
----------
Do not add `from __future__ import annotations` to any action module. This will cause class types to be resolved as strings."""

# Bring all actions into the namespace to be registered
from tracecat.experimental.actions.core import core, example  # noqa: I001
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

from tracecat.experimental.actions._registry import registry

__all__ = [
    "registry",
    "core",
    "example",
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
