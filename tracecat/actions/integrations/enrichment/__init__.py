"""Unified integrations for enrichment.

Supported Capabilities:
- Enrich URL
- Enrich IP address (IPv4, IPv6)
- Enrich Malware Sample (MD5, SHA1, SHA256)

Base OCSF Object Schema: https://schema.ocsf.io/1.2.0/objects/observable
"""

from . import virustotal

__all__ = ["virustotal"]
