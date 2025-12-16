"""Tests for core.cases UDFs using the registry SDK client."""

from __future__ import annotations

import base64
from typing import Any

import httpx
import pytest
import respx
from tracecat_registry.core import cases as core_cases
from tracecat_registry.sdk.client import TracecatClient


@pytest.mark.anyio
async def test_create_case_posts_to_cases(monkeypatch: pytest.MonkeyPatch) -> None:
    async def fake_post(self: TracecatClient, path: str, *, params=None, json=None):  # type: ignore[no-untyped-def]
        assert path == "/cases"
        assert params is None
        assert json == {
            "summary": "S",
            "status": "new",
            "priority": "medium",
            "severity": "high",
            "description": "D",
            "fields": {"field": "value"},
            "payload": {"p": 1},
            "tags": ["tag-1"],
        }
        return {"id": "case-1"}

    monkeypatch.setattr(TracecatClient, "post", fake_post, raising=True)

    result = await core_cases.create_case(
        summary="S",
        description="D",
        status="new",
        priority="medium",
        severity="high",
        fields={"field": "value"},
        payload={"p": 1},
        tags=["tag-1"],
    )
    assert result == {"id": "case-1"}


@pytest.mark.anyio
async def test_update_case_appends_description(monkeypatch: pytest.MonkeyPatch) -> None:
    calls: list[tuple[str, str, Any]] = []

    async def fake_get(self: TracecatClient, path: str, *, params=None):  # type: ignore[no-untyped-def]
        calls.append(("GET", path, params))
        assert path == "/cases/case-1"
        return {"id": "case-1", "description": "Existing"}

    async def fake_patch(self: TracecatClient, path: str, *, params=None, json=None):  # type: ignore[no-untyped-def]
        calls.append(("PATCH", path, json))
        assert path == "/cases/case-1"
        assert json == {"description": "Existing\nNew"}
        return {"id": "case-1", "description": "Existing\nNew"}

    monkeypatch.setattr(TracecatClient, "get", fake_get, raising=True)
    monkeypatch.setattr(TracecatClient, "patch", fake_patch, raising=True)

    result = await core_cases.update_case(
        case_id="case-1", description="New", append=True
    )
    assert result["description"] == "Existing\nNew"
    assert [c[0] for c in calls] == ["GET", "PATCH"]


@pytest.mark.anyio
async def test_update_case_replaces_tags(monkeypatch: pytest.MonkeyPatch) -> None:
    calls: list[tuple[str, str, Any]] = []

    async def fake_get(self: TracecatClient, path: str, *, params=None):  # type: ignore[no-untyped-def]
        calls.append(("GET", path, params))
        if path == "/cases/case-1/tags":
            return [{"id": "old-1"}, {"id": "old-2"}]
        raise AssertionError(f"Unexpected GET {path}")

    async def fake_delete(self: TracecatClient, path: str, *, params=None):  # type: ignore[no-untyped-def]
        calls.append(("DELETE", path, params))
        assert path in {
            "/cases/case-1/tags/old-1",
            "/cases/case-1/tags/old-2",
        }
        return None

    async def fake_post(self: TracecatClient, path: str, *, params=None, json=None):  # type: ignore[no-untyped-def]
        calls.append(("POST", path, json))
        assert path == "/cases/case-1/tags"
        assert json in ({"tag_id": "new-1"}, {"tag_id": "new-2"})
        return None

    async def fake_patch(self: TracecatClient, path: str, *, params=None, json=None):  # type: ignore[no-untyped-def]
        calls.append(("PATCH", path, json))
        assert path == "/cases/case-1"
        return {"ok": True}

    monkeypatch.setattr(TracecatClient, "get", fake_get, raising=True)
    monkeypatch.setattr(TracecatClient, "delete", fake_delete, raising=True)
    monkeypatch.setattr(TracecatClient, "post", fake_post, raising=True)
    monkeypatch.setattr(TracecatClient, "patch", fake_patch, raising=True)

    result = await core_cases.update_case(case_id="case-1", tags=["new-1", "new-2"])
    assert result == {"ok": True}

    methods = [m for (m, _, _) in calls]
    assert methods.count("GET") == 1
    assert methods.count("DELETE") == 2
    assert methods.count("POST") == 2
    assert methods.count("PATCH") == 1


@pytest.mark.anyio
async def test_list_cases_returns_page_items(monkeypatch: pytest.MonkeyPatch) -> None:
    async def fake_get(self: TracecatClient, path: str, *, params=None):  # type: ignore[no-untyped-def]
        assert path == "/cases"
        assert params == {"limit": 10, "order_by": "created_at", "sort": "desc"}
        return {"items": [{"id": "c1"}, {"id": "c2"}]}

    monkeypatch.setattr(TracecatClient, "get", fake_get, raising=True)
    items = await core_cases.list_cases(limit=10, order_by="created_at", sort="desc")
    assert items == [{"id": "c1"}, {"id": "c2"}]


@pytest.mark.anyio
async def test_search_cases_builds_expected_params(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    async def fake_get(self: TracecatClient, path: str, *, params=None):  # type: ignore[no-untyped-def]
        assert path == "/cases/search"
        assert params == {
            "search_term": "foo",
            "status": ["new"],
            "priority": ["high"],
            "severity": ["critical"],
            "tags": ["tag-1"],
            "limit": 25,
            "order_by": "updated_at",
            "sort": "asc",
        }
        return [{"id": "c1"}]

    monkeypatch.setattr(TracecatClient, "get", fake_get, raising=True)
    result = await core_cases.search_cases(
        search_term="foo",
        status="new",
        priority="high",
        severity="critical",
        tags=["tag-1"],
        limit=25,
        order_by="updated_at",
        sort="asc",
    )
    assert result == [{"id": "c1"}]


@pytest.mark.anyio
async def test_get_case_uses_client_directly(monkeypatch: pytest.MonkeyPatch) -> None:
    async def fake_get(self: TracecatClient, path: str, *, params=None):  # type: ignore[no-untyped-def]
        assert path == "/cases/case-1"
        return {"id": "case-1"}

    monkeypatch.setattr(TracecatClient, "get", fake_get, raising=True)
    assert await core_cases.get_case("case-1") == {"id": "case-1"}


@pytest.mark.anyio
async def test_create_comment_posts_to_case_comments(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    async def fake_post(self: TracecatClient, path: str, *, params=None, json=None):  # type: ignore[no-untyped-def]
        assert path == "/cases/case-1/comments"
        assert json == {"content": "hello"}
        return {"id": "comment-1", "content": "hello"}

    monkeypatch.setattr(TracecatClient, "post", fake_post, raising=True)
    assert await core_cases.create_comment(case_id="case-1", content="hello") == {
        "id": "comment-1",
        "content": "hello",
    }


@pytest.mark.anyio
async def test_assign_user_updates_case_assignee(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    async def fake_patch(self: TracecatClient, path: str, *, params=None, json=None):  # type: ignore[no-untyped-def]
        assert path == "/cases/case-1"
        assert json == {"assignee_id": "user-1"}
        return {"id": "case-1", "assignee_id": "user-1"}

    monkeypatch.setattr(TracecatClient, "patch", fake_patch, raising=True)
    assert await core_cases.assign_user(case_id="case-1", assignee_id="user-1") == {
        "id": "case-1",
        "assignee_id": "user-1",
    }


@pytest.mark.anyio
async def test_assign_user_by_email_calls_users_search_then_patches_case(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    calls: list[tuple[str, str, Any]] = []

    async def fake_get(self: TracecatClient, path: str, *, params=None):  # type: ignore[no-untyped-def]
        calls.append(("GET", path, params))
        if path == "/users/search":
            assert params == {"email": "a@b.com"}
            return {"id": "user-1"}
        if path == "/cases/case-1":
            return {"id": "case-1", "assignee_id": "user-1"}
        raise AssertionError(f"Unexpected GET {path}")

    async def fake_patch(self: TracecatClient, path: str, *, params=None, json=None):  # type: ignore[no-untyped-def]
        calls.append(("PATCH", path, json))
        assert path == "/cases/case-1"
        assert json == {"assignee_id": "user-1"}
        return {"ok": True}

    monkeypatch.setattr(TracecatClient, "get", fake_get, raising=True)
    monkeypatch.setattr(TracecatClient, "patch", fake_patch, raising=True)

    result = await core_cases.assign_user_by_email(
        case_id="case-1", assignee_email="a@b.com"
    )
    assert result["assignee_id"] == "user-1"
    assert [c[0] for c in calls] == ["GET", "PATCH", "GET"]


@pytest.mark.anyio
async def test_add_case_tag_success(monkeypatch: pytest.MonkeyPatch) -> None:
    async def fake_post(self: TracecatClient, path: str, *, params=None, json=None):  # type: ignore[no-untyped-def]
        assert path == "/cases/case-1/tags"
        assert json == {"tag_id": "tag-1"}
        return None

    monkeypatch.setattr(TracecatClient, "post", fake_post, raising=True)
    assert await core_cases.add_case_tag(case_id="case-1", tag="tag-1") == {"ok": True}


@pytest.mark.anyio
async def test_add_case_tag_creates_when_missing(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    state = {"attempt": 0}

    async def fake_post(self: TracecatClient, path: str, *, params=None, json=None):  # type: ignore[no-untyped-def]
        if path == "/cases/case-1/tags":
            state["attempt"] += 1
            if state["attempt"] == 1:
                raise Exception("missing")  # noqa: BLE001 - emulate API failure
            assert json == {"tag_id": "created-1"}
            return None
        if path == "/case-tags":
            assert json == {"name": "tag-name"}
            return {"id": "created-1"}
        raise AssertionError(f"Unexpected POST {path}")

    monkeypatch.setattr(TracecatClient, "post", fake_post, raising=True)
    assert await core_cases.add_case_tag(
        case_id="case-1", tag="tag-name", create_if_missing=True
    ) == {"ok": True}


@pytest.mark.anyio
async def test_remove_case_tag_deletes_tag(monkeypatch: pytest.MonkeyPatch) -> None:
    async def fake_delete(self: TracecatClient, path: str, *, params=None):  # type: ignore[no-untyped-def]
        assert path == "/cases/case-1/tags/tag-1"
        return None

    monkeypatch.setattr(TracecatClient, "delete", fake_delete, raising=True)
    await core_cases.remove_case_tag(case_id="case-1", tag="tag-1")


@pytest.mark.anyio
async def test_delete_case_calls_delete(monkeypatch: pytest.MonkeyPatch) -> None:
    async def fake_delete(self: TracecatClient, path: str, *, params=None):  # type: ignore[no-untyped-def]
        assert path == "/cases/case-1"
        assert params is None
        return None

    monkeypatch.setattr(TracecatClient, "delete", fake_delete, raising=True)
    await core_cases.delete_case(case_id="case-1")


@pytest.mark.anyio
async def test_list_case_events_gets_events(monkeypatch: pytest.MonkeyPatch) -> None:
    async def fake_get(self: TracecatClient, path: str, *, params=None):  # type: ignore[no-untyped-def]
        assert path == "/cases/case-1/events"
        assert params is None
        return {"events": [], "users": []}

    monkeypatch.setattr(TracecatClient, "get", fake_get, raising=True)
    assert await core_cases.list_case_events(case_id="case-1") == {
        "events": [],
        "users": [],
    }


@pytest.mark.anyio
async def test_list_comments_gets_comments(monkeypatch: pytest.MonkeyPatch) -> None:
    async def fake_get(self: TracecatClient, path: str, *, params=None):  # type: ignore[no-untyped-def]
        assert path == "/cases/case-1/comments"
        return [{"id": "c1"}]

    monkeypatch.setattr(TracecatClient, "get", fake_get, raising=True)
    assert await core_cases.list_comments(case_id="case-1") == [{"id": "c1"}]


@pytest.mark.anyio
async def test_list_attachments_gets_attachments(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    async def fake_get(self: TracecatClient, path: str, *, params=None):  # type: ignore[no-untyped-def]
        assert path == "/cases/case-1/attachments"
        return [{"id": "a1"}]

    monkeypatch.setattr(TracecatClient, "get", fake_get, raising=True)
    assert await core_cases.list_attachments(case_id="case-1") == [{"id": "a1"}]


@pytest.mark.anyio
async def test_get_attachment_gets_attachment(monkeypatch: pytest.MonkeyPatch) -> None:
    async def fake_get(self: TracecatClient, path: str, *, params=None):  # type: ignore[no-untyped-def]
        assert path == "/cases/case-1/attachments/att-1"
        assert params is None
        return {"id": "att-1", "download_url": "https://example.com/dl"}

    monkeypatch.setattr(TracecatClient, "get", fake_get, raising=True)
    assert await core_cases.get_attachment(case_id="case-1", attachment_id="att-1") == {
        "id": "att-1",
        "download_url": "https://example.com/dl",
    }


@pytest.mark.anyio
async def test_get_attachment_download_url_passes_expiry(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    async def fake_get(self: TracecatClient, path: str, *, params=None):  # type: ignore[no-untyped-def]
        assert path == "/cases/case-1/attachments/att-1"
        assert params == {"expiry": 60}
        return {"download_url": "https://example.com/dl"}

    monkeypatch.setattr(TracecatClient, "get", fake_get, raising=True)
    assert (
        await core_cases.get_attachment_download_url(
            case_id="case-1",
            attachment_id="att-1",
            expiry=60,
        )
        == "https://example.com/dl"
    )


@pytest.mark.anyio
async def test_get_attachment_download_url_validates_expiry() -> None:
    with pytest.raises(ValueError, match="positive"):
        await core_cases.get_attachment_download_url(
            case_id="case-1",
            attachment_id="att-1",
            expiry=0,
        )
    with pytest.raises(ValueError, match="24 hours"):
        await core_cases.get_attachment_download_url(
            case_id="case-1",
            attachment_id="att-1",
            expiry=86401,
        )


@pytest.mark.anyio
async def test_download_attachment_gets_base64_content(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    async def fake_get(self: TracecatClient, path: str, *, params=None):  # type: ignore[no-untyped-def]
        assert path == "/cases/case-1/attachments/att-1/download"
        return {
            "content_base64": "Zm9v",
            "file_name": "a.txt",
            "content_type": "text/plain",
        }

    monkeypatch.setattr(TracecatClient, "get", fake_get, raising=True)
    assert await core_cases.download_attachment(
        case_id="case-1", attachment_id="att-1"
    ) == {
        "content_base64": "Zm9v",
        "file_name": "a.txt",
        "content_type": "text/plain",
    }


@pytest.mark.anyio
async def test_delete_attachment_deletes(monkeypatch: pytest.MonkeyPatch) -> None:
    async def fake_delete(self: TracecatClient, path: str, *, params=None):  # type: ignore[no-untyped-def]
        assert path == "/cases/case-1/attachments/att-1"
        return None

    monkeypatch.setattr(TracecatClient, "delete", fake_delete, raising=True)
    await core_cases.delete_attachment(case_id="case-1", attachment_id="att-1")


@pytest.mark.anyio
async def test_upload_attachment_posts_base64_payload(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    async def fake_post(self: TracecatClient, path: str, *, params=None, json=None):  # type: ignore[no-untyped-def]
        assert path == "/cases/case-1/attachments"
        assert json == {
            "filename": "a.txt",
            "content_base64": "Zm9v",
            "content_type": "text/plain",
        }
        return {"id": "att-1"}

    monkeypatch.setattr(TracecatClient, "post", fake_post, raising=True)
    assert await core_cases.upload_attachment(
        case_id="case-1",
        file_name="a.txt",
        content_base64="Zm9v",
        content_type="text/plain",
    ) == {"id": "att-1"}


@pytest.mark.anyio
@respx.mock
async def test_upload_attachment_from_url_downloads_and_uploads(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    respx.get("https://example.com/files/report.txt").mock(
        return_value=httpx.Response(
            status_code=200,
            headers={"Content-Type": "text/plain"},
            content=b"hello",
        )
    )

    async def fake_post(self: TracecatClient, path: str, *, params=None, json=None):  # type: ignore[no-untyped-def]
        assert path == "/cases/case-1/attachments"
        assert json is not None
        assert json["filename"] == "report.txt"
        assert json["content_type"] == "text/plain"
        assert json["content_base64"] == base64.b64encode(b"hello").decode()
        return {"id": "att-1"}

    monkeypatch.setattr(TracecatClient, "post", fake_post, raising=True)

    result = await core_cases.upload_attachment_from_url(
        case_id="case-1",
        url="https://example.com/files/report.txt",
    )
    assert result == {"id": "att-1"}
