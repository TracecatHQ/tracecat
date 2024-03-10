import re
from functools import partial

import pytest

from tracecat.actions import (
    DEFAULT_TEMPLATE_PATTERN,
    evaluate_jsonpath_str,
    evaluate_templated_fields,
    run_send_email_action,
)


def test_evaluate_jsonpath_str_raises_exception():
    matcher = partial(re.match, DEFAULT_TEMPLATE_PATTERN)
    json_data = {
        "question_generation": {
            "questions": [
                "What is the capital of France?",
                "What is the capital of Germany?",
            ],
        },
        "receive_sentry_event": {
            "event_id": "123",
        },
        "list_nested": [
            {
                "a": "1",
                "b": "2",
            },
            {
                "a": "3",
                "b": "4",
            },
        ],
        "list_nested_different_types": [
            {
                "a": 1,
                "b": 2,
            },
            {
                "a": "3",
                "b": "4",
            },
        ],
    }
    with pytest.raises(ValueError):
        # Invalid jsonpath
        match = matcher("{{ .bad_jsonpath }}")
        evaluate_jsonpath_str(match, json_data)


def test_evaluate_jsonpath_str():
    matcher = partial(re.match, DEFAULT_TEMPLATE_PATTERN)
    json_data = {
        "question_generation": {
            "questions": [
                "What is the capital of France?",
                "What is the capital of Germany?",
            ],
        },
        "receive_sentry_event": {
            "event_id": "123",
        },
        "list_nested": [
            {
                "a": "1",
                "b": "2",
            },
            {
                "a": "3",
                "b": "4",
            },
        ],
        "list_nested_different_types": [
            {
                "a": 1,
                "b": 2,
            },
            {
                "a": "3",
                "b": "4",
            },
        ],
    }
    match = matcher("{{ $.question_generation.questions[0] }}")
    expected = "What is the capital of France?"
    assert evaluate_jsonpath_str(match, json_data) == expected

    match = matcher("{{ $.receive_sentry_event.event_id }}")
    expected = "123"
    assert evaluate_jsonpath_str(match, json_data) == expected

    match = matcher("{{ $.question_generation.questions }}")
    expected = str(json_data["question_generation"]["questions"])
    assert evaluate_jsonpath_str(match, json_data) == expected

    match = matcher("{{ $.list_nested[*].a }}")
    expected = "['1', '3']"
    assert evaluate_jsonpath_str(match, json_data) == expected

    match = matcher("{{ $.list_nested_different_types[*].b }}")
    expected = "[2, '4']"
    assert evaluate_jsonpath_str(match, json_data) == expected


def test_evaluate_templated_fields_no_match():
    json_data = {
        "workspace": {
            "name": "Tracecat",
            "channel": "general",
            "visibility": "public",
        },
    }
    kwargs = {
        "title": "My ticket title",
    }

    expected_kwargs = {
        "title": "My ticket title",
    }
    actual_kwargs = evaluate_templated_fields(kwargs, json_data)
    assert actual_kwargs == expected_kwargs


def test_evaluate_templated_fields():
    json_data = {
        "ticket_sections": {
            "questions": [
                "What does the error on line 122 mean?",
                "How can I improve the performance of my code?",
            ],
        },
        "receive_sentry_event": {
            "event_id": 123123,
        },
        "workspace": {
            "name": "Tracecat",
            "channel": "general",
            "visibility": "public",
        },
    }
    kwargs = {
        "title": "My ticket title",
        "event_id": "I had a event occur with ID {{ $.receive_sentry_event.event_id }}. Any ideas?",
        "question": "Hey, {{ $.ticket_sections.questions[0] }}. I really need the help?",
        "slack": {
            "{{ $.workspace.visibility }}_workspaces": [
                {
                    "name": "{{ $.workspace.name }}",
                    "channel": "{{ $.workspace.channel }}",
                }
            ],
        },
    }

    expected_kwargs = {
        "title": "My ticket title",
        "event_id": "I had a event occur with ID 123123. Any ideas?",
        "question": "Hey, What does the error on line 122 mean?. I really need the help?",
        "slack": {
            "public_workspaces": [
                {
                    "name": "Tracecat",
                    "channel": "general",
                }
            ],
        },
    }
    actual_kwargs = evaluate_templated_fields(kwargs, json_data)
    assert actual_kwargs == expected_kwargs


def test_evaluate_templated_fields_matches_multiple_in_string():
    json_data = {
        "question_generation": {
            "questions": [
                "What is the capital of France?",
                "What is the capital of Germany?",
            ],
        },
        "receive_sentry_event": {
            "event_id": "123",
        },
    }
    templated_string = "My questions {{ $.question_generation.questions[0] }}, my sentry event: {{ $.receive_sentry_event.event_id }}"

    exptected = "My questions What is the capital of France?, my sentry event: 123"
    actual = DEFAULT_TEMPLATE_PATTERN.sub(
        lambda m: evaluate_jsonpath_str(m, json_data), templated_string
    )
    assert actual == exptected


def test_evaluate_templated_fields_matches_multiple_different_types():
    mock_json_data = {
        "question_generation": {
            "questions": [
                "What is the capital of France?",
                "What is the capital of Germany?",
            ],
        },
        "receive_sentry_event": {
            "event_id": "123",
            "timestamp": 1234567890,
        },
    }
    mock_templated_kwargs = {
        "questions": [
            "My questions {{ $.question_generation.questions[0] }}, my sentry event: {{ $.receive_sentry_event.event_id }}",
            "Last question: {{ $.question_generation.questions[1] }}",
        ],
        "observation": {
            "details": "The event occurred at {{ $.receive_sentry_event.timestamp }}",
        },
    }

    exptected = {
        "questions": [
            "My questions What is the capital of France?, my sentry event: 123",
            "Last question: What is the capital of Germany?",
        ],
        "observation": {
            "details": "The event occurred at 1234567890",
        },
    }
    actual = evaluate_templated_fields(
        action_kwargs=mock_templated_kwargs, action_trail_json=mock_json_data
    )
    assert actual == exptected


def test_evaluate_templated_fields_raises_exception():
    mock_json_data = {
        "question_generation": {
            "questions": [
                "What is the capital of France?",
                "What is the capital of Germany?",
            ],
        },
        "receive_sentry_event": {
            "event_id": "123",
        },
    }
    mock_templated_kwargs = {
        "questions": "My questions {{ $.nonexistent.field }}, my sentry event: {{ $.receive_sentry_event.event_id }}"
    }

    with pytest.raises(ValueError):
        evaluate_templated_fields(
            action_kwargs=mock_templated_kwargs, action_trail_json=mock_json_data
        )


@pytest.mark.webtest
@pytest.mark.asyncio
async def test_send_email_action_all_success():
    test_sender = "Mewtwo <test@tracecat.com>"
    recipients = ["chris+test@tracecat.com", "daryl+test@tracecat.com"]
    subject = "[TEST] Hello from Tracecat!"
    body = "Meow! 🐱"
    email_response = await run_send_email_action(
        sender=test_sender,
        recipients=recipients,
        subject=subject,
        body=body,
    )
    assert email_response == {
        "status": "ok",
        "message": "Successfully sent email",
        "provider": "resend",
        "sender": test_sender,
        "recipients": recipients,
        "subject": subject,
        "body": body,
    }


@pytest.mark.webtest
@pytest.mark.asyncio
async def test_send_email_action_unrecognized_provider():
    test_sender = "Mewtwo <test@tracecat.com>"
    recipients = ["chris+test@tracecat.com", "daryl+test@tracecat.com"]
    subject = "[TEST] Hello from Tracecat!"
    body = "Meow! 🐱"
    provider = "e-meow"
    email_response = await run_send_email_action(
        sender=test_sender,
        recipients=recipients,
        subject=subject,
        body=body,
        provider=provider,
    )
    assert email_response == {
        "status": "error",
        "message": "Email provider not recognized",
        "provider": provider,
        "sender": test_sender,
        "recipients": recipients,
        "subject": subject,
        "body": body,
    }


@pytest.mark.webtest
@pytest.mark.asyncio
async def test_send_email_action_bounced():
    test_sender = "Mewtwo <test@tracecat.com>"
    recipients = ["doesnotexist@tracecat.com"]
    subject = "[TEST] Hello from Tracecat!"
    body = "Meow! 🐱"
    email_response = await run_send_email_action(
        sender=test_sender,
        recipients=recipients,
        subject=subject,
        body=body,
    )
    assert email_response == {
        "status": "warning",
        "message": "Email bounced",
        "provider": "resend",
        "sender": test_sender,
        "recipients": recipients,
        "subject": subject,
        "body": body,
    }
