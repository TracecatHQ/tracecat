import uuid

import pytest

from tracecat.cases.enums import MentionTargetType
from tracecat.cases.mentions import MentionToken, parse_mentions

SINGLE_MENTION_TARGET_ID = uuid.UUID("00000000-0000-4000-8000-000000000001")
UNKNOWN_TARGET_TYPE_ID = uuid.UUID("00000000-0000-4000-8000-000000000002")
UNTERMINATED_TOKEN_TARGET_ID = uuid.UUID("00000000-0000-4000-8000-000000000003")
MISSING_BRACKET_TARGET_ID = uuid.UUID("00000000-0000-4000-8000-000000000004")
WRONG_SCHEME_TARGET_ID = uuid.UUID("00000000-0000-4000-8000-000000000005")
DUPLICATE_MENTION_TARGET_ID = uuid.UUID("00000000-0000-4000-8000-000000000006")
FIRST_DISTINCT_TARGET_ID = uuid.UUID("00000000-0000-4000-8000-000000000007")
SECOND_DISTINCT_TARGET_ID = uuid.UUID("00000000-0000-4000-8000-000000000008")
ADJACENT_TEXT_TARGET_ID = uuid.UUID("00000000-0000-4000-8000-000000000009")
UNICODE_LABEL_TARGET_ID = uuid.UUID("00000000-0000-4000-8000-000000000010")
MAX_LENGTH_LABEL_TARGET_ID = uuid.UUID("00000000-0000-4000-8000-000000000011")
OVERLONG_LABEL_TARGET_ID = uuid.UUID("00000000-0000-4000-8000-000000000012")


def test_parse_single_valid_mention() -> None:
    target_id = SINGLE_MENTION_TARGET_ID

    assert parse_mentions(f"[@Response agent](mention://agent/{target_id})") == [
        MentionToken(
            target_type=MentionTargetType.AGENT,
            target_id=target_id,
            label="Response agent",
        )
    ]


@pytest.mark.parametrize(
    "content",
    [
        "[@Agent](mention://agent/not-a-uuid)",
        f"[@User](mention://user/{UNKNOWN_TARGET_TYPE_ID})",
        f"[@Agent](mention://agent/{UNTERMINATED_TOKEN_TARGET_ID}",
        f"[@Agent(mention://agent/{MISSING_BRACKET_TARGET_ID})",
        f"[@Agent](mentions://agent/{WRONG_SCHEME_TARGET_ID})",
    ],
)
def test_parse_skips_malformed_mentions(content: str) -> None:
    assert parse_mentions(content) == []


def test_parse_preserves_duplicate_mentions() -> None:
    target_id = DUPLICATE_MENTION_TARGET_ID
    content = (
        f"[@First label](mention://agent/{target_id}) "
        f"[@Second label](mention://agent/{target_id})"
    )

    assert parse_mentions(content) == [
        MentionToken(
            target_type=MentionTargetType.AGENT,
            target_id=target_id,
            label="First label",
        ),
        MentionToken(
            target_type=MentionTargetType.AGENT,
            target_id=target_id,
            label="Second label",
        ),
    ]


def test_parse_multiple_distinct_mentions() -> None:
    first_target_id = FIRST_DISTINCT_TARGET_ID
    second_target_id = SECOND_DISTINCT_TARGET_ID
    content = (
        f"[@First](mention://agent/{first_target_id}) and "
        f"[@Second](mention://agent/{second_target_id})"
    )

    assert parse_mentions(content) == [
        MentionToken(
            target_type=MentionTargetType.AGENT,
            target_id=first_target_id,
            label="First",
        ),
        MentionToken(
            target_type=MentionTargetType.AGENT,
            target_id=second_target_id,
            label="Second",
        ),
    ]


def test_parse_mention_adjacent_to_surrounding_text() -> None:
    target_id = ADJACENT_TEXT_TARGET_ID

    assert parse_mentions(f"before[@Agent](mention://agent/{target_id})after") == [
        MentionToken(
            target_type=MentionTargetType.AGENT, target_id=target_id, label="Agent"
        )
    ]


def test_parse_preserves_unicode_label() -> None:
    target_id = UNICODE_LABEL_TARGET_ID
    label = "响应者 🚨 агент"

    assert parse_mentions(f"[@{label}](mention://agent/{target_id})") == [
        MentionToken(
            target_type=MentionTargetType.AGENT, target_id=target_id, label=label
        )
    ]


def test_parse_accepts_label_at_max_length() -> None:
    target_id = MAX_LENGTH_LABEL_TARGET_ID
    label = "a" * 255

    assert parse_mentions(f"[@{label}](mention://agent/{target_id})") == [
        MentionToken(
            target_type=MentionTargetType.AGENT, target_id=target_id, label=label
        )
    ]


def test_parse_skips_overlong_label() -> None:
    target_id = OVERLONG_LABEL_TARGET_ID
    label = "a" * 256

    assert parse_mentions(f"[@{label}](mention://agent/{target_id})") == []
