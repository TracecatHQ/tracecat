"""Integrations module."""

# Import modules to register integrations
from tracecat.integrations import (
    aws_cloudtrail,
    aws_guardduty,
    datadog,
    emailrep,
    project_discovery,
    sublime,
    urlscan,
    virustotal,
)
from tracecat.integrations._meta import IntegrationSpec
from tracecat.integrations._registry import registry

__all__ = [
    "IntegrationSpec",
    "registry",
    # Integrations
    "aws_cloudtrail",
    "aws_guardduty",
    "datadog",
    "emailrep",
    "project_discovery",
    "sublime",
    "urlscan",
    "virustotal",
]
