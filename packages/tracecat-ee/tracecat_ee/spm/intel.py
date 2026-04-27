"""Best-effort threat intelligence lookups for SPM analyzer evaluations."""

from __future__ import annotations

import base64
import uuid
from pathlib import Path
from typing import Any, Protocol
from urllib.parse import urlparse

import httpx
from sqlalchemy.ext.asyncio import AsyncSession

from tracecat.auth.types import Role
from tracecat.exceptions import TracecatNotFoundError
from tracecat.secrets.service import SecretsService

_GITHUB_SECURITY_ADVISORIES_QUERY = """
query SecurityVulnerabilities($ecosystem: SecurityAdvisoryEcosystem!, $package: String!, $first: Int!) {
  securityVulnerabilities(ecosystem: $ecosystem, package: $package, first: $first) {
    nodes {
      severity
      vulnerableVersionRange
      firstPatchedVersion {
        identifier
      }
      advisory {
        ghsaId
        summary
        permalink
        severity
        withdrawnAt
      }
    }
  }
}
"""

_PACKAGE_MANAGER_ECOSYSTEMS = {
    "npx": "npm",
    "npm": "npm",
    "pnpm": "npm",
    "yarn": "npm",
    "uvx": "PyPI",
    "pipx": "PyPI",
    "python": "PyPI",
    "python3": "PyPI",
}
_GITHUB_ECOSYSTEMS = {
    "npm": "NPM",
    "PyPI": "PIP",
}


class SpmThreatIntelProvider(Protocol):
    """Lookup interface used by the analyzer."""

    async def enrich_mcp_server(
        self,
        *,
        metadata: dict[str, Any],
        evidence: dict[str, Any],
    ) -> dict[str, Any]: ...

    async def enrich_instruction_file(
        self,
        *,
        metadata: dict[str, Any],
        evidence: dict[str, Any],
    ) -> dict[str, Any]: ...


class NoopSpmThreatIntelProvider:
    """Threat intel provider used when enrichment is disabled or unavailable."""

    async def enrich_mcp_server(
        self,
        *,
        metadata: dict[str, Any],
        evidence: dict[str, Any],
    ) -> dict[str, Any]:
        _ = metadata, evidence
        return {}

    async def enrich_instruction_file(
        self,
        *,
        metadata: dict[str, Any],
        evidence: dict[str, Any],
    ) -> dict[str, Any]:
        _ = metadata, evidence
        return {}


class BestEffortSpmThreatIntelProvider:
    """Secret-backed threat intel lookups with graceful degradation."""

    def __init__(
        self,
        session: AsyncSession,
        *,
        organization_id: uuid.UUID,
        timeout: float = 10.0,
    ) -> None:
        self.session = session
        self.organization_id = organization_id
        self.timeout = timeout
        self._secret_cache: dict[str, dict[str, str] | None] = {}

    async def enrich_mcp_server(
        self,
        *,
        metadata: dict[str, Any],
        evidence: dict[str, Any],
    ) -> dict[str, Any]:
        resolved_identity = _string(metadata.get("resolved_identity"))
        if not resolved_identity:
            return {}

        package_name, ecosystem = _package_coordinate(
            resolved_identity=resolved_identity,
            metadata=metadata,
        )
        osv = await self._lookup_osv(package_name=package_name, ecosystem=ecosystem)
        github = await self._lookup_github_advisories(
            package_name=package_name,
            ecosystem=ecosystem,
        )
        virustotal = await self._lookup_indicator_bundle(
            urls=_http_strings(
                [
                    resolved_identity,
                    evidence.get("resolved_identity"),
                    evidence.get("resolved_url"),
                ]
            ),
            domains=_domain_strings(
                [metadata.get("origin"), metadata.get("resolved_origin")]
            ),
            ips=_normalized_strings([]),
        )

        vulnerability_status = _aggregate_status(
            [osv.get("status"), github.get("status")]
        )
        reputation_status = virustotal.get("status")

        return {
            "resolved_identity": resolved_identity,
            "package": {
                "name": package_name,
                "ecosystem": ecosystem,
            },
            "vulnerability_status": vulnerability_status,
            "reputation_status": reputation_status,
            "osv": osv,
            "github_advisories": github,
            "virustotal": virustotal,
        }

    async def enrich_instruction_file(
        self,
        *,
        metadata: dict[str, Any],
        evidence: dict[str, Any],
    ) -> dict[str, Any]:
        _ = metadata
        urls = _normalized_strings(evidence.get("urls"))
        domains = _normalized_strings(evidence.get("domains"))
        ips = _normalized_strings(evidence.get("ips"))
        virustotal = await self._lookup_indicator_bundle(
            urls=urls,
            domains=domains,
            ips=ips,
        )
        indicator_reputation_status = virustotal.get("status")
        bad_indicators = _merge_bad_indicators(virustotal)
        return {
            "indicator_reputation_status": indicator_reputation_status,
            "bad_indicators": bad_indicators,
            "virustotal": virustotal,
        }

    def _role(self) -> Role:
        return Role(
            type="service",
            service_id="tracecat-api",
            organization_id=self.organization_id,
            scopes=frozenset({"org:secret:read"}),
        )

    async def _get_secret_values(
        self,
        *secret_names: str,
    ) -> dict[str, str] | None:
        service = SecretsService(self.session, role=self._role())
        for secret_name in secret_names:
            if secret_name in self._secret_cache:
                values = self._secret_cache[secret_name]
                if values is not None:
                    return values
                continue

            try:
                secret = await service.get_org_secret_by_name(secret_name)
            except TracecatNotFoundError:
                self._secret_cache[secret_name] = None
                continue

            values = {
                item.key: item.value.get_secret_value()
                for item in service.decrypt_keys(secret.encrypted_keys)
            }
            self._secret_cache[secret_name] = values
            return values
        return None

    async def _lookup_osv(
        self,
        *,
        package_name: str | None,
        ecosystem: str | None,
    ) -> dict[str, Any]:
        if not package_name or not ecosystem:
            return {"status": None, "matches": []}

        data = await self._request_json(
            method="POST",
            url="https://api.osv.dev/v1/query",
            json={
                "package": {
                    "name": package_name,
                    "ecosystem": ecosystem,
                }
            },
        )
        vulns = data.get("vulns", []) if isinstance(data, dict) else []
        matches = [
            {
                "id": item.get("id"),
                "summary": item.get("summary"),
                "aliases": item.get("aliases", []),
            }
            for item in vulns
            if isinstance(item, dict)
        ]
        return {
            "status": "bad" if matches else "good",
            "matches": matches,
        }

    async def _lookup_github_advisories(
        self,
        *,
        package_name: str | None,
        ecosystem: str | None,
    ) -> dict[str, Any]:
        github_ecosystem = _GITHUB_ECOSYSTEMS.get(ecosystem or "")
        if not package_name or not github_ecosystem:
            return {"status": None, "advisories": []}

        secret_values = await self._get_secret_values("github", "github_token")
        token = (
            None
            if secret_values is None
            else (
                secret_values.get("GITHUB_TOKEN")
                or secret_values.get("TOKEN")
                or secret_values.get("token")
            )
        )
        if not token:
            return {"status": None, "advisories": []}

        data = await self._request_json(
            method="POST",
            url="https://api.github.com/graphql",
            headers={
                "Authorization": f"Bearer {token}",
                "Accept": "application/vnd.github+json",
            },
            json={
                "query": _GITHUB_SECURITY_ADVISORIES_QUERY,
                "variables": {
                    "ecosystem": github_ecosystem,
                    "package": package_name,
                    "first": 20,
                },
            },
        )
        nodes = (
            (
                ((data.get("data") or {}).get("securityVulnerabilities") or {}).get(
                    "nodes"
                )
            )
            if isinstance(data, dict)
            else None
        )
        advisories = []
        if isinstance(nodes, list):
            for item in nodes:
                if not isinstance(item, dict):
                    continue
                advisory = item.get("advisory") or {}
                if advisory.get("withdrawnAt"):
                    continue
                advisories.append(
                    {
                        "ghsa_id": advisory.get("ghsaId"),
                        "summary": advisory.get("summary"),
                        "permalink": advisory.get("permalink"),
                        "severity": advisory.get("severity") or item.get("severity"),
                        "vulnerable_version_range": item.get("vulnerableVersionRange"),
                        "first_patched_version": (
                            (item.get("firstPatchedVersion") or {}).get("identifier")
                            if isinstance(item.get("firstPatchedVersion"), dict)
                            else None
                        ),
                    }
                )
        return {
            "status": "bad" if advisories else "good",
            "advisories": advisories,
        }

    async def _lookup_indicator_bundle(
        self,
        *,
        urls: list[str],
        domains: list[str],
        ips: list[str],
    ) -> dict[str, Any]:
        secret_values = await self._get_secret_values("virustotal")
        api_key = (
            None if secret_values is None else secret_values.get("VIRUSTOTAL_API_KEY")
        )
        if not api_key:
            return {"status": None, "matches": []}

        matches: list[dict[str, Any]] = []
        for url in urls[:5]:
            if match := await self._lookup_virustotal_url(api_key=api_key, url=url):
                matches.append(match)
        for domain in domains[:5]:
            if match := await self._lookup_virustotal_domain(
                api_key=api_key,
                domain=domain,
            ):
                matches.append(match)
        for ip in ips[:5]:
            if match := await self._lookup_virustotal_ip(api_key=api_key, ip=ip):
                matches.append(match)

        return {
            "status": _aggregate_status([item.get("status") for item in matches]),
            "matches": matches,
        }

    async def _lookup_virustotal_url(
        self,
        *,
        api_key: str,
        url: str,
    ) -> dict[str, Any] | None:
        encoded = (
            base64.urlsafe_b64encode(url.encode("utf-8")).decode("utf-8").rstrip("=")
        )
        data = await self._request_json(
            method="GET",
            url=f"https://www.virustotal.com/api/v3/urls/{encoded}",
            headers={"x-apikey": api_key},
        )
        return _virustotal_match(
            indicator=url,
            indicator_type="url",
            payload=data,
        )

    async def _lookup_virustotal_domain(
        self,
        *,
        api_key: str,
        domain: str,
    ) -> dict[str, Any] | None:
        data = await self._request_json(
            method="GET",
            url=f"https://www.virustotal.com/api/v3/domains/{domain}",
            headers={"x-apikey": api_key},
        )
        return _virustotal_match(
            indicator=domain,
            indicator_type="domain",
            payload=data,
        )

    async def _lookup_virustotal_ip(
        self,
        *,
        api_key: str,
        ip: str,
    ) -> dict[str, Any] | None:
        data = await self._request_json(
            method="GET",
            url=f"https://www.virustotal.com/api/v3/ip_addresses/{ip}",
            headers={"x-apikey": api_key},
        )
        return _virustotal_match(
            indicator=ip,
            indicator_type="ip",
            payload=data,
        )

    async def _request_json(
        self,
        *,
        method: str,
        url: str,
        headers: dict[str, str] | None = None,
        params: dict[str, Any] | None = None,
        json: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        try:
            async with httpx.AsyncClient(
                timeout=self.timeout,
                follow_redirects=True,
            ) as client:
                response = await client.request(
                    method,
                    url,
                    headers=headers,
                    params=params,
                    json=json,
                )
                response.raise_for_status()
        except httpx.HTTPError:
            return {}

        try:
            payload = response.json()
        except ValueError:
            return {}
        return payload if isinstance(payload, dict) else {}


def _package_coordinate(
    *,
    resolved_identity: str,
    metadata: dict[str, Any],
) -> tuple[str | None, str | None]:
    if not resolved_identity.startswith("package:"):
        return None, None
    command = _string(metadata.get("command"))
    package_name = resolved_identity.removeprefix("package:")
    if not package_name:
        return None, None
    ecosystem = None
    if command:
        ecosystem = _PACKAGE_MANAGER_ECOSYSTEMS.get(Path(command).name)
    return package_name, ecosystem


def _normalized_strings(raw: Any) -> list[str]:
    if isinstance(raw, str):
        return [raw] if raw else []
    if not isinstance(raw, list):
        return []
    return [item for item in raw if isinstance(item, str) and item]


def _http_strings(raw: Any) -> list[str]:
    return [
        item
        for item in _normalized_strings(raw)
        if item.startswith(("http://", "https://"))
    ]


def _domain_strings(raw: Any) -> list[str]:
    domains: list[str] = []
    for item in _normalized_strings(raw):
        parsed = urlparse(item)
        if parsed.hostname:
            domains.append(parsed.hostname)
        else:
            domains.append(item)
    return domains


def _string(raw: Any) -> str | None:
    if isinstance(raw, str) and raw:
        return raw
    return None


def _aggregate_status(statuses: list[str | None]) -> str | None:
    values = [status for status in statuses if status in {"good", "bad"}]
    if not values:
        return None
    if "bad" in values:
        return "bad"
    return "good"


def _virustotal_match(
    *,
    indicator: str,
    indicator_type: str,
    payload: dict[str, Any],
) -> dict[str, Any] | None:
    attributes = (payload.get("data") or {}).get("attributes")
    if not isinstance(attributes, dict):
        return None
    stats = attributes.get("last_analysis_stats") or {}
    if not isinstance(stats, dict):
        stats = {}
    malicious = int(stats.get("malicious") or 0)
    suspicious = int(stats.get("suspicious") or 0)
    status = "bad" if malicious > 0 or suspicious > 0 else "good"
    return {
        "indicator": indicator,
        "type": indicator_type,
        "status": status,
        "stats": stats,
    }


def _merge_bad_indicators(*sources: dict[str, Any]) -> list[dict[str, Any]]:
    merged: list[dict[str, Any]] = []
    for source in sources:
        for match in source.get("matches", []):
            if isinstance(match, dict) and (
                match.get("status") == "bad" or source.get("status") == "bad"
            ):
                merged.append(match)
    return merged
