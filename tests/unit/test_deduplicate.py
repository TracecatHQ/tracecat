"""Unit tests for workspace-scoped deduplication.

Tests cover:
- Internal router: check-and-set endpoint with mocked Redis
- SDK client: exact path regression test
- Transform: persist=True uses SDK, persist=False does not
"""

from __future__ import annotations

import hashlib
import json
from typing import Any
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from tracecat_registry.core.transform import (
    _compute_digests,
    deduplicate,
    is_duplicate,
)

# ---------------------------------------------------------------------------
# _compute_digests
# ---------------------------------------------------------------------------


class TestComputeDigests:
    """Tests for the digest computation helper."""

    def test_deterministic(self) -> None:
        """Same key tuple produces the same digest."""
        seen: dict[tuple[Any, ...], dict[str, Any]] = {
            ("a", 1): {"id": 1},
            ("b", 2): {"id": 2},
        }
        assert _compute_digests(seen) == _compute_digests(seen)

    def test_matches_sha256(self) -> None:
        """Digest matches manual SHA256 of JSON-serialized key."""
        key = ("alert_id", 42)
        seen: dict[tuple[Any, ...], dict[str, Any]] = {key: {"x": 1}}
        key_str = json.dumps(key, sort_keys=True, default=str)
        expected = hashlib.sha256(key_str.encode()).hexdigest()
        assert _compute_digests(seen) == [expected]

    def test_preserves_order(self) -> None:
        """Digests are returned in iteration order of the dict."""
        seen: dict[tuple[Any, ...], dict[str, Any]] = {
            ("first",): {"id": 1},
            ("second",): {"id": 2},
            ("third",): {"id": 3},
        }
        digests = _compute_digests(seen)
        assert len(digests) == 3
        # Each digest is unique
        assert len(set(digests)) == 3


# ---------------------------------------------------------------------------
# SDK client path regression
# ---------------------------------------------------------------------------


class TestDeduplicateClientPath:
    """Regression test: SDK client calls the correct API path."""

    @pytest.mark.anyio
    async def test_create_digests_path(self) -> None:
        """SDK posts to /deduplicate/digests with correct payload."""
        from tracecat_registry.sdk.deduplicate import DeduplicateClient

        mock_client = AsyncMock()
        mock_client.post = AsyncMock(return_value={"created": [True, False]})

        client = DeduplicateClient(mock_client)
        result = await client.create_digests(["abc123", "def456"], 3600)

        mock_client.post.assert_called_once_with(
            "/deduplicate/digests",
            json={"digests": ["abc123", "def456"], "expire_seconds": 3600},
        )
        assert result == [True, False]


# ---------------------------------------------------------------------------
# Transform: deduplicate with persist=True / persist=False
# ---------------------------------------------------------------------------


class TestDeduplicateTransform:
    """Tests for the deduplicate registry action's SDK integration."""

    @pytest.mark.anyio
    async def test_persist_true_calls_sdk(self) -> None:
        """persist=True delegates to the deduplicate SDK client."""
        mock_dedup_client = AsyncMock()
        mock_dedup_client.create_digests = AsyncMock(return_value=[True, False])

        mock_ctx = MagicMock()
        mock_ctx.deduplicate = mock_dedup_client

        items = [{"id": 1, "v": "a"}, {"id": 2, "v": "b"}]

        with patch(
            "tracecat_registry.core.transform.get_context", return_value=mock_ctx
        ):
            result = await deduplicate(items, keys=["id"], persist=True)

        mock_dedup_client.create_digests.assert_called_once()
        # First item inserted (True), second not (False)
        assert result == [{"id": 1, "v": "a"}]

    @pytest.mark.anyio
    async def test_persist_false_does_not_call_sdk(self) -> None:
        """persist=False does within-call dedup only, no SDK call."""
        mock_ctx = MagicMock()

        with patch(
            "tracecat_registry.core.transform.get_context", return_value=mock_ctx
        ):
            result = await deduplicate(
                [{"id": 1}, {"id": 1}, {"id": 2}],
                keys=["id"],
                persist=False,
            )

        # SDK should not be touched
        mock_ctx.deduplicate.create_digests.assert_not_called()
        # Within-call dedup: two unique items
        assert len(result) == 2

    @pytest.mark.anyio
    async def test_is_duplicate_uses_sdk(self) -> None:
        """is_duplicate delegates to deduplicate(persist=True)."""
        mock_dedup_client = AsyncMock()
        # Return empty list = item was already seen = duplicate
        mock_dedup_client.create_digests = AsyncMock(return_value=[False])

        mock_ctx = MagicMock()
        mock_ctx.deduplicate = mock_dedup_client

        with patch(
            "tracecat_registry.core.transform.get_context", return_value=mock_ctx
        ):
            result = await is_duplicate({"id": 1}, keys=["id"])

        assert result is True
        mock_dedup_client.create_digests.assert_called_once()

    @pytest.mark.anyio
    async def test_is_duplicate_new_item(self) -> None:
        """is_duplicate returns False for a newly inserted item."""
        mock_dedup_client = AsyncMock()
        mock_dedup_client.create_digests = AsyncMock(return_value=[True])

        mock_ctx = MagicMock()
        mock_ctx.deduplicate = mock_dedup_client

        with patch(
            "tracecat_registry.core.transform.get_context", return_value=mock_ctx
        ):
            result = await is_duplicate({"id": 99}, keys=["id"])

        assert result is False

    @pytest.mark.anyio
    async def test_empty_items_skips_sdk(self) -> None:
        """Empty input returns empty list without calling SDK."""
        mock_ctx = MagicMock()

        with patch(
            "tracecat_registry.core.transform.get_context", return_value=mock_ctx
        ):
            result = await deduplicate([], keys=["id"], persist=True)

        assert result == []
        mock_ctx.deduplicate.create_digests.assert_not_called()


# ---------------------------------------------------------------------------
# Internal router: check-and-set endpoint
# ---------------------------------------------------------------------------


class TestCheckAndSetRouter:
    """Tests for the deduplicate internal router endpoint."""

    @staticmethod
    def _make_role(workspace_id: str) -> MagicMock:
        """Create a mock role with executor scopes for require_scope."""
        role = MagicMock()
        role.workspace_id = workspace_id
        role.scopes = frozenset({"deduplicate:create"})
        return role

    @pytest.mark.anyio
    async def test_create_digests_sequential(self) -> None:
        """Small batch uses sequential set_if_not_exists calls."""
        from tracecat.contexts import ctx_role
        from tracecat.deduplicate.internal_router import (
            CreateDigestsRequest,
            create_digests,
        )

        mock_redis = AsyncMock()
        mock_redis.set_if_not_exists = AsyncMock(side_effect=[True, False, True])

        role = self._make_role("ws-123")
        request = CreateDigestsRequest(
            digests=["aaa", "bbb", "ccc"], expire_seconds=3600
        )

        token = ctx_role.set(role)
        try:
            with patch(
                "tracecat.deduplicate.internal_router.get_redis_client",
                return_value=mock_redis,
            ):
                response = await create_digests(role=role, request=request)
        finally:
            ctx_role.reset(token)

        assert response.created == [True, False, True]
        assert mock_redis.set_if_not_exists.call_count == 3

        # Verify workspace-scoped key format
        calls = mock_redis.set_if_not_exists.call_args_list
        assert calls[0].args[0] == "dedup:ws-123:aaa"
        assert calls[1].args[0] == "dedup:ws-123:bbb"
        assert calls[2].args[0] == "dedup:ws-123:ccc"

    @pytest.mark.anyio
    async def test_create_digests_pipeline(self) -> None:
        """Large batch (>10) uses pipeline path."""
        from tracecat.contexts import ctx_role
        from tracecat.deduplicate.internal_router import (
            CreateDigestsRequest,
            create_digests,
        )

        mock_pipe = AsyncMock()
        mock_pipe.set = MagicMock()
        mock_pipe.execute = AsyncMock(return_value=[True] * 5 + [False] * 6)

        mock_raw_client = AsyncMock()
        mock_raw_client.pipeline = MagicMock(return_value=mock_pipe)

        mock_redis = AsyncMock()
        mock_redis._get_client = AsyncMock(return_value=mock_raw_client)

        role = self._make_role("ws-456")
        digests = [f"digest_{i:03d}" for i in range(11)]
        request = CreateDigestsRequest(digests=digests, expire_seconds=7200)

        token = ctx_role.set(role)
        try:
            with patch(
                "tracecat.deduplicate.internal_router.get_redis_client",
                return_value=mock_redis,
            ):
                response = await create_digests(role=role, request=request)
        finally:
            ctx_role.reset(token)

        assert response.created == [True] * 5 + [False] * 6
        mock_raw_client.pipeline.assert_called_once_with(transaction=False)
        assert mock_pipe.set.call_count == 11

    @pytest.mark.anyio
    async def test_workspace_isolation(self) -> None:
        """Different workspaces get different Redis key prefixes."""
        from tracecat.contexts import ctx_role
        from tracecat.deduplicate.internal_router import (
            CreateDigestsRequest,
            create_digests,
        )

        captured_keys: list[str] = []

        async def capture_set(key: str, value: str, *, expire_seconds: int) -> bool:
            captured_keys.append(key)
            return True

        mock_redis = AsyncMock()
        mock_redis.set_if_not_exists = AsyncMock(side_effect=capture_set)

        request = CreateDigestsRequest(digests=["same_digest"], expire_seconds=60)

        for ws_id in ["ws-aaa", "ws-bbb"]:
            role = self._make_role(ws_id)
            token = ctx_role.set(role)
            try:
                with patch(
                    "tracecat.deduplicate.internal_router.get_redis_client",
                    return_value=mock_redis,
                ):
                    await create_digests(role=role, request=request)
            finally:
                ctx_role.reset(token)

        assert captured_keys == [
            "dedup:ws-aaa:same_digest",
            "dedup:ws-bbb:same_digest",
        ]

    def test_request_validation_empty_digests(self) -> None:
        """Empty digests list is rejected by Pydantic validation."""
        from pydantic import ValidationError

        from tracecat.deduplicate.internal_router import CreateDigestsRequest

        with pytest.raises(ValidationError):
            CreateDigestsRequest(digests=[], expire_seconds=3600)

    def test_request_validation_ttl_bounds(self) -> None:
        """TTL must be between 1 and 2592000."""
        from pydantic import ValidationError

        from tracecat.deduplicate.internal_router import CreateDigestsRequest

        with pytest.raises(ValidationError):
            CreateDigestsRequest(digests=["abc"], expire_seconds=0)

        with pytest.raises(ValidationError):
            CreateDigestsRequest(digests=["abc"], expire_seconds=2592001)
