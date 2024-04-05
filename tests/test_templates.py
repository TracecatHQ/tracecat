import os
import re
from functools import partial

import pytest
import respx
from fastapi.testclient import TestClient
from httpx import Response

from tracecat.config import TRACECAT__API_URL
from tracecat.db import Secret
from tracecat.runner.app import app
from tracecat.runner.templates import (
    JSONPATH_TEMPLATE_PATTERN,
    SECRET_TEMPLATE_PATTERN,
    _evaluate_jsonpath_str,
    _evaluate_secret_str,
    evaluate_templated_fields,
    evaluate_templated_secrets,
)

client = TestClient(app)


@pytest.fixture(autouse=True)
def setup_templates_env():
    from tracecat.contexts import ctx_workflow
    from tracecat.runner.workflows import Workflow

    os.environ["TEST_API_KEY_1"] = "1234567890"
    os.environ["test_api_key_2"] = "asdfghjkl"

    mock_workflow = Workflow(
        title="Test Workflow", adj_list={}, actions={}, owner_id="test_user_id"
    )
    # We need to set the workflow context for the tests
    ctx_workflow.set(mock_workflow)
    yield


@pytest.mark.asyncio
async def test_evaluate_secret_string():
    mock_secret_manager = {
        "TEST_API_KEY_1": "not_so_secret",
        "ANOTHER_SECRET": "not_a_secret",
    }

    async def mock_secret_getter(secret_name: str):
        return mock_secret_manager[secret_name]

    mock_templated_string = (
        "This is a {{ SECRETS.TEST_API_KEY_1 }} secret {{ SECRETS.ANOTHER_SECRET }}"
    )
    expected = "This is a not_so_secret secret not_a_secret"
    actual = await _evaluate_secret_str(
        mock_templated_string,
        template_pattern=SECRET_TEMPLATE_PATTERN,
        secret_getter=mock_secret_getter,
    )
    assert actual == expected


@pytest.mark.asyncio
async def test_evaluate_templated_secret():
    # Health check
    client.get("/")

    TEST_SECRETS = {
        "TEST_API_KEY_1": "1234567890",
        "test_api_key_2": "asdfghjkl",
    }

    mock_templated_kwargs = {
        "question_generation": {
            "questions": [
                "This is a {{ SECRETS.TEST_API_KEY_1 }} secret {{ SECRETS.test_api_key_2 }}",
                "This is a {{ SECRETS.test_api_key_2 }} secret",
            ],
        },
        "receive_sentry_event": {
            "event_id": "This is a {{ SECRETS.TEST_API_KEY_1 }} secret",
        },
        "list_nested": [
            {
                "a": "Test {{ SECRETS.TEST_API_KEY_1 }} #A",
                "b": "Test {{ SECRETS.test_api_key_2 }} #B",
            },
            {
                "a": "3",
                "b": "4",
            },
        ],
    }
    exptected = {
        "question_generation": {
            "questions": [
                "This is a 1234567890 secret asdfghjkl",
                "This is a asdfghjkl secret",
            ],
        },
        "receive_sentry_event": {
            "event_id": "This is a 1234567890 secret",
        },
        "list_nested": [
            {
                "a": "Test 1234567890 #A",
                "b": "Test asdfghjkl #B",
            },
            {
                "a": "3",
                "b": "4",
            },
        ],
    }

    base_secrets_url = f"{TRACECAT__API_URL}/secrets"
    with respx.mock:
        # Mock workflow getter from API side
        for secret_name, secret_value in TEST_SECRETS.items():
            secret = Secret(
                name=secret_name,
                owner_id="test_user_id",
            )
            secret.key = secret_value  # Encrypt the secret

            # Mock hitting get secrets endpoint
            print(secret)
            respx.get(f"{base_secrets_url}/{secret_name}").mock(
                return_value=Response(
                    200,
                    json=secret.model_dump(mode="json"),
                )
            )

        # Start test
        actual = await evaluate_templated_secrets(
            templated_fields=mock_templated_kwargs
        )
    assert actual == exptected


def test_evaluate_jsonpath_str_raises_exception():
    matcher = partial(re.match, JSONPATH_TEMPLATE_PATTERN)
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
        _evaluate_jsonpath_str(match, json_data)


def test_evaluate_jsonpath_str():
    matcher = partial(re.match, JSONPATH_TEMPLATE_PATTERN)
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
    assert _evaluate_jsonpath_str(match, json_data) == expected

    match = matcher("{{ $.receive_sentry_event.event_id }}")
    expected = "123"
    assert _evaluate_jsonpath_str(match, json_data) == expected

    match = matcher("{{ $.question_generation.questions }}")
    expected = str(json_data["question_generation"]["questions"])
    assert _evaluate_jsonpath_str(match, json_data) == expected

    match = matcher("{{ $.list_nested[*].a }}")
    expected = "['1', '3']"
    assert _evaluate_jsonpath_str(match, json_data) == expected

    match = matcher("{{ $.list_nested_different_types[*].b }}")
    expected = "[2, '4']"
    assert _evaluate_jsonpath_str(match, json_data) == expected


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
    actual_kwargs = evaluate_templated_fields(
        templated_fields=kwargs, source_data=json_data
    )
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
    actual_kwargs = evaluate_templated_fields(
        templated_fields=kwargs, source_data=json_data
    )
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
    actual = JSONPATH_TEMPLATE_PATTERN.sub(
        lambda m: _evaluate_jsonpath_str(m, json_data), templated_string
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
        templated_fields=mock_templated_kwargs, source_data=mock_json_data
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
            templated_fields=mock_templated_kwargs, source_data=mock_json_data
        )
