import uuid

import pytest

from tracecat.cases.mentions import MentionToken, parse_mentions


def test_parse_single_valid_mention() -> None:
    target_id = uuid.uuid4()

    assert parse_mentions(f"[@Response agent](mention://agent/{target_id})") == [
        MentionToken(
            target_type="agent",
            target_id=target_id,
            label="Response agent",
        )
    ]


@pytest.mark.parametrize(
    "content",
    [
        "[@Agent](mention://agent/not-a-uuid)",
        f"[@User](mention://user/{uuid.uuid4()})",
        f"[@Agent](mention://agent/{uuid.uuid4()}",
        f"[@Agent(mention://agent/{uuid.uuid4()})",
        f"[@Agent](mentions://agent/{uuid.uuid4()})",
    ],
)
def test_parse_skips_malformed_mentions(content: str) -> None:
    assert parse_mentions(content) == []


def test_parse_preserves_duplicate_mentions() -> None:
    target_id = uuid.uuid4()
    content = (
        f"[@First label](mention://agent/{target_id}) "
        f"[@Second label](mention://agent/{target_id})"
    )

    assert parse_mentions(content) == [
        MentionToken(target_type="agent", target_id=target_id, label="First label"),
        MentionToken(target_type="agent", target_id=target_id, label="Second label"),
    ]


def test_parse_multiple_distinct_mentions() -> None:
    first_target_id = uuid.uuid4()
    second_target_id = uuid.uuid4()
    content = (
        f"[@First](mention://agent/{first_target_id}) and "
        f"[@Second](mention://agent/{second_target_id})"
    )

    assert parse_mentions(content) == [
        MentionToken(
            target_type="agent",
            target_id=first_target_id,
            label="First",
        ),
        MentionToken(
            target_type="agent",
            target_id=second_target_id,
            label="Second",
        ),
    ]


def test_parse_mention_adjacent_to_surrounding_text() -> None:
    target_id = uuid.uuid4()

    assert parse_mentions(f"before[@Agent](mention://agent/{target_id})after") == [
        MentionToken(target_type="agent", target_id=target_id, label="Agent")
    ]


def test_parse_preserves_unicode_label() -> None:
    target_id = uuid.uuid4()
    label = "响应者 🚨 агент"

    assert parse_mentions(f"[@{label}](mention://agent/{target_id})") == [
        MentionToken(target_type="agent", target_id=target_id, label=label)
    ]
