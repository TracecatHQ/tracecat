"""Actions module for tracecat.

WARNING!!!
----------
Do not add `from __future__ import annotations` to any action module. This will cause class types to be resolved as strings."""

# Bring all actions into the namespace to be registered
import os

from tracecat.actions.core import cases, condition, email, example, http, llm, transform

__all__ = [
    # Core
    "example",
    "http",
    "email",
    "llm",
    "condition",
    "cases",
    "transform",
]


if str(os.environ.get("TRACECAT__ENABLE_INTEGRATIONS")).lower() in ("true", "1"):
    from tracecat.actions.integrations import (
        aws_cloudtrail,  # noqa: F401
        aws_guardduty,  # noqa: F401
        datadog,  # noqa: F401
        emailrep,  # noqa: F401
        example_integration,  # noqa: F401
        project_discovery,  # noqa: F401
        sublime,  # noqa: F401
        urlscan,  # noqa: F401
        virustotal,  # noqa: F401
    )

    __all__.extend(
        [
            "example_integration",
            "datadog",
            "aws_cloudtrail",
            "aws_guardduty",
            "sublime",
            "urlscan",
            "project_discovery",
            "emailrep",
            "virustotal",
        ]
    )
