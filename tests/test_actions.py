import re
from functools import partial

from tracecat.actions import (
    DEFAULT_TEMPLATE_PATTERN,
    evaluate_jsonpath_str,
    evaluate_templated_fields,
)


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
    # Bad matches

    match = matcher("{{ $.nonexistent_field }}")
    expected = "{{ $.nonexistent_field }}"
    assert evaluate_jsonpath_str(match, json_data) == expected


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
        "remarks": "I would like to also leearn {{ $.nonexistent_field }}.",
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
        "remarks": "I would like to also leearn {{ $.nonexistent_field }}.",
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


def test_evaluate_jsonpath_str_matches_multiple():
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
