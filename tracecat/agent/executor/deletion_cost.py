"""Best-effort Kubernetes pod deletion cost publishing."""

from __future__ import annotations

import asyncio
import os
from pathlib import Path
from typing import TypedDict

import httpx

from tracecat import config
from tracecat.logger import logger

SERVICE_ACCOUNT_TOKEN_PATH = Path("/var/run/secrets/kubernetes.io/serviceaccount/token")
SERVICE_ACCOUNT_CA_PATH = Path("/var/run/secrets/kubernetes.io/serviceaccount/ca.crt")
POD_DELETION_COST_ANNOTATION = "controller.kubernetes.io/pod-deletion-cost"
PUBLISH_TIMEOUT_SECONDS = 5.0
MAX_CONSECUTIVE_FAILURES = 3


class _PodMetadataPatch(TypedDict):
    annotations: dict[str, str]


class _PodPatch(TypedDict):
    metadata: _PodMetadataPatch


class PodDeletionCostPublisher:
    """Publish this process's in-flight agent turn count to its Kubernetes pod."""

    def __init__(self) -> None:
        self._api_host = os.environ.get("KUBERNETES_SERVICE_HOST") or ""
        self._api_port = os.environ.get("KUBERNETES_SERVICE_PORT") or "443"
        self._pod_name = (config.TRACECAT__K8S_POD_NAME or "").strip()
        self._pod_namespace = (config.TRACECAT__K8S_POD_NAMESPACE or "").strip()
        self._enabled = bool(
            config.TRACECAT__AGENT_EXECUTOR_POD_DELETION_COST_ENABLED
            and self._api_host
            and SERVICE_ACCOUNT_TOKEN_PATH.exists()
            and self._pod_name
            and self._pod_namespace
        )
        self._count = 0
        self._last_published_count: int | None = None
        self._consecutive_failures = 0
        self._publishing = False
        self._lock = asyncio.Lock()

        if not self._enabled:
            logger.debug("Kubernetes pod deletion cost publisher disabled")

    async def increment(self) -> None:
        """Add one in-flight agent turn and publish the latest count."""
        await self._adjust(1)

    async def decrement(self) -> None:
        """Remove one in-flight agent turn and publish the latest count."""
        await self._adjust(-1)

    async def _adjust(self, delta: int) -> None:
        if not self._enabled:
            return

        async with self._lock:
            if not self._enabled:
                return
            self._count += delta
            if self._publishing:
                return
            self._publishing = True

        try:
            # Give changes scheduled in the same event-loop turn a chance to
            # coalesce before taking the first snapshot.
            await asyncio.sleep(0)
            await self._publish_latest()
        except BaseException:
            async with self._lock:
                self._publishing = False
            raise

    async def _publish_latest(self) -> None:
        while self._enabled:
            async with self._lock:
                count = self._count
                if count == self._last_published_count:
                    self._publishing = False
                    return

            published = await self._publish(count)

            async with self._lock:
                if published:
                    self._last_published_count = count
                if not self._enabled or self._count == count:
                    self._publishing = False
                    return

    async def _publish(self, count: int) -> bool:
        url = (
            f"https://{self._api_host}:{self._api_port}/api/v1/namespaces/"
            f"{self._pod_namespace}/pods/{self._pod_name}"
        )
        patch: _PodPatch = {
            "metadata": {
                "annotations": {
                    POD_DELETION_COST_ANNOTATION: str(count),
                }
            }
        }

        try:
            token = SERVICE_ACCOUNT_TOKEN_PATH.read_text().strip()
            async with httpx.AsyncClient(
                verify=str(SERVICE_ACCOUNT_CA_PATH),
                timeout=PUBLISH_TIMEOUT_SECONDS,
            ) as client:
                response = await client.patch(
                    url,
                    headers={
                        "Authorization": f"Bearer {token}",
                        "Content-Type": "application/merge-patch+json",
                    },
                    json=patch,
                )
                response.raise_for_status()
        except httpx.HTTPStatusError as exc:
            self._record_failure(
                status_code=exc.response.status_code,
                error=str(exc),
            )
            return False
        except (httpx.HTTPError, OSError) as exc:
            self._record_failure(status_code=None, error=str(exc))
            return False

        self._consecutive_failures = 0
        return True

    def _record_failure(self, *, status_code: int | None, error: str) -> None:
        self._consecutive_failures += 1
        if self._consecutive_failures >= MAX_CONSECUTIVE_FAILURES:
            self._enabled = False
            logger.warning(
                "Disabling Kubernetes pod deletion cost publisher after "
                "consecutive failures",
                failures=self._consecutive_failures,
                status_code=status_code,
                error=error,
            )
            return

        logger.warning(
            "Failed to publish Kubernetes pod deletion cost",
            failures=self._consecutive_failures,
            status_code=status_code,
            error=error,
        )


# Lazy singleton - no lifespan required.
_pod_deletion_cost_publisher: PodDeletionCostPublisher | None = None


def get_pod_deletion_cost_publisher() -> PodDeletionCostPublisher:
    """Get the global pod deletion cost publisher instance."""
    global _pod_deletion_cost_publisher
    if _pod_deletion_cost_publisher is None:
        _pod_deletion_cost_publisher = PodDeletionCostPublisher()
    return _pod_deletion_cost_publisher
