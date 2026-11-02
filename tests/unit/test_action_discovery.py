"""Action catalog pagination behavior across visibility and index changes."""

from types import SimpleNamespace
from typing import Any

import pytest

from tracecat.mcp.action_discovery import list_action_entry_page


def _entry(namespace: str, name: str, *, visible: bool = True) -> tuple[Any, str]:
    return (
        SimpleNamespace(
            namespace=namespace,
            name=name,
            options={"include_in_schema": visible},
        ),
        "platform",
    )


@pytest.mark.anyio
async def test_search_excludes_hidden_actions_before_paging() -> None:
    class Service:
        async def search_actions_from_index(
            self, _query: str, *, limit: int | None = None
        ) -> list[tuple[Any, str]]:
            assert limit is None
            return [
                _entry("tools.example", "hidden", visible=False),
                _entry("tools.example", "visible"),
            ]

    service: Any = Service()
    page = await list_action_entry_page(service, query="example", limit=1)

    assert [(entry.namespace, entry.name) for entry, _ in page.entries] == [
        ("tools.example", "visible")
    ]
    assert page.has_more is False


@pytest.mark.anyio
async def test_browse_namespace_prefix_is_literal() -> None:
    class Service:
        async def list_actions_from_index(
            self, *, namespace: str | None = None
        ) -> list[tuple[Any, str]]:
            assert namespace == "tools.example_"
            # SQL LIKE can treat the trailing underscore as a wildcard.
            return [
                _entry("tools.example_", "first"),
                _entry("tools.exampleX", "unrelated"),
            ]

    service: Any = Service()
    page = await list_action_entry_page(service, namespace="tools.example_")

    assert [(entry.namespace, entry.name) for entry, _ in page.entries] == [
        ("tools.example_", "first")
    ]


@pytest.mark.anyio
async def test_cursor_continues_after_index_entry_before_anchor_is_removed() -> None:
    class Service:
        entries = [_entry("tools.example", f"event_{index:03d}") for index in range(4)]

        async def search_actions_from_index(
            self, _query: str, *, limit: int | None = None
        ) -> list[tuple[Any, str]]:
            assert limit is None
            return self.entries

    service: Any = Service()
    first = await list_action_entry_page(service, query="example", limit=2)
    service.entries.pop(0)
    second = await list_action_entry_page(
        service, query="example", limit=2, cursor=first.next_cursor
    )

    assert [entry.name for entry, _ in second.entries] == [
        "event_002",
        "event_003",
    ]
    assert second.has_more is False
    assert second.has_previous is True
