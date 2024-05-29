import os

import pytest
import respx
from fastapi.testclient import TestClient
from httpx import Response

from tracecat import config
from tracecat.contexts import ctx_role
from tracecat.db.helpers import batch_get_secrets, format_secrets_as_json
from tracecat.db.models import Secret
from tracecat.experimental.templates.eval import (
    eval_templated_object,
    extract_templated_secrets,
)
from tracecat.experimental.templates.future import TemplatedFuture, eval_jsonpath
from tracecat.types.secrets import SecretKeyValue


@pytest.fixture(scope="session")
def mock_api(monkeysession, env_sandbox, tracecat_stack):
    monkeysession.setattr(config, "TRACECAT__DB_URI", os.environ["TRACECAT__DB_URI"])
    from tracecat.api.app import app

    with TestClient(app) as client:
        yield client


@pytest.mark.asyncio
def test_eval_jsonpath():
    operand = {"webhook": {"result": 42, "data": {"name": "John", "age": 30}}}
    assert eval_jsonpath("$.webhook.result", operand) == 42
    assert eval_jsonpath("$.webhook.data.name", operand) == "John"
    assert eval_jsonpath("$.webhook.data.age", operand) == 30
    with pytest.raises(ValueError) as e:
        _ = eval_jsonpath("$.webhook.data.nonexistent", operand) is None
        assert "Operand has no path" in str(e.value)


@pytest.mark.parametrize(
    "expression, expected_result",
    [
        ("${{ ACTIONS.webhook.result -> int }}", 1),
        ("${{ INPUTS.arg1 -> int }}", 1),
        ("${{ INPUTS.arg1 }}", "1"),  # Default to str
        ("${{ INPUTS.arg2 -> str }}", "2"),
        ("${{ ACTIONS.webhook.result -> str }}", "1"),
        ("${{ ACTIONS.path_A_first.result.path.nested.value -> int }}", 9999),
    ],
)
def test_templated_expression_result(expression, expected_result):
    exec_vars = {
        "INPUTS": {
            "arg1": 1,
            "arg2": 2,
        },
        "ACTIONS": {
            "webhook": {"result": 1},
            "path_A_first": {"result": {"path": {"nested": {"value": 9999}}}},
            "path_A_second": {"result": 3},
            "path_B_first": {"result": 4},
            "path_B_second": {"result": 5},
        },
        "metadata": {"name": "John Doe", "age": 30},
    }

    fut = TemplatedFuture(expression, operand=exec_vars)
    assert fut.result() == expected_result


def test_find_secrets():
    # Test for finding secrets in a string
    test_str = "This is a ${{ SECRETS.my_secret.TEST_API_KEY_1 }} secret"
    expected = ["my_secret.TEST_API_KEY_1"]

    assert extract_templated_secrets(test_str) == expected

    mock_templated_kwargs = {
        "question_generation": {
            "questions": [
                "This is a ${{ SECRETS.my_secret.TEST_API_KEY_1 }} secret ${{ SECRETS.other_secret.test_api_key_2 }}",
                "This is a ${{ SECRETS.other_secret.test_api_key_2 }} secret",
            ],
        },
        "receive_sentry_event": {
            "event_id": "This is a ${{ SECRETS.my_secret.TEST_API_KEY_1 }} secret",
        },
        "list_nested": [
            {
                "a": "Test ${{ SECRETS.my_secret.TEST_API_KEY_1 }} #A",
                "b": "Test ${{ SECRETS.other_secret.test_api_key_2 }} #B",
            },
            {
                "a": "3",
                "b": "4",
            },
        ],
    }

    expected = ["my_secret.TEST_API_KEY_1", "other_secret.test_api_key_2"]
    assert sorted(extract_templated_secrets(mock_templated_kwargs)) == sorted(expected)


@pytest.mark.asyncio
async def test_evaluate_templated_secret(mock_api, auth_sandbox):
    # Health check
    mock_api.get("/")
    TEST_SECRETS = {
        "my_secret": [
            SecretKeyValue(key="TEST_API_KEY_1", value="1234567890"),
            SecretKeyValue(key="NOISE_1", value="asdfasdf"),
        ],
        "other_secret": [
            SecretKeyValue(key="test_api_key_2", value="@@@@@@@@@"),
            SecretKeyValue(key="NOISE_2", value="aaaaaaaaaaaaa"),
        ],
    }

    mock_templated_kwargs = {
        "question_generation": {
            "questions": [
                "This is a ${{ SECRETS.my_secret.TEST_API_KEY_1 }} secret ${{ SECRETS.other_secret.test_api_key_2 }}",
                "This is a ${{ SECRETS.other_secret.test_api_key_2 }} secret",
            ],
        },
        "receive_sentry_event": {
            "event_id": "This is a ${{ SECRETS.my_secret.TEST_API_KEY_1 }} secret",
        },
        "list_nested": [
            {
                "a": "Test ${{ SECRETS.my_secret.TEST_API_KEY_1 }} #A",
                "b": "Test ${{ SECRETS.other_secret.test_api_key_2 }} #B",
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
                "This is a 1234567890 secret @@@@@@@@@",
                "This is a @@@@@@@@@ secret",
            ],
        },
        "receive_sentry_event": {
            "event_id": "This is a 1234567890 secret",
        },
        "list_nested": [
            {
                "a": "Test 1234567890 #A",
                "b": "Test @@@@@@@@@ #B",
            },
            {
                "a": "3",
                "b": "4",
            },
        ],
    }

    base_secrets_url = f"{config.TRACECAT__API_URL}/secrets"
    with respx.mock:
        # Mock workflow getter from API side
        for secret_name, secret_keys in TEST_SECRETS.items():
            secret = Secret(
                type="custom",
                name=secret_name,
                owner_id="test_user_id",
            )
            secret.keys = secret_keys  # Encrypt the secret

            # Mock hitting get secrets endpoint
            respx.get(f"{base_secrets_url}/{secret_name}").mock(
                return_value=Response(
                    200,
                    json=secret.model_dump(mode="json"),
                )
            )

        # Start test
        secret_paths = extract_templated_secrets(mock_templated_kwargs)
        secret_names = [path.split(".")[0] for path in secret_paths]
        secrets = await batch_get_secrets(ctx_role.get(), secret_names)
        secret_ctx = {"SECRETS": format_secrets_as_json(secrets)}
        actual = eval_templated_object(obj=mock_templated_kwargs, operand=secret_ctx)
    assert actual == exptected


def test_eval_templated_object():
    data = {
        "ACTIONS": {
            "webhook": {
                "result": 42,
                "url": "https://example.com",
                "count": 3,
            }
        },
        "SECRETS": {
            "my_secret": "@@@",
        },
    }
    templates = [
        {
            "test": {
                "data": "INLINE: ${{ ACTIONS.webhook.result }}",
                "url": "${{ ACTIONS.webhook.url}}",
                "number": "${{ ACTIONS.webhook.result -> int }}",
                "number_whitespace": "${{ ACTIONS.webhook.result -> int }}",
                # Inline substitution resulting in a string cannot cast to int
                "number_whitespace_multi_string": " ${{ ACTIONS.webhook.result }} ${{ ACTIONS.webhook.count }}  ",
            }
        },
        "Inline substitution ${{ ACTIONS.webhook.result}} like this",
        "${{ ACTIONS.webhook.url}}",
        "            Again, inline substitution ${{ SECRETS.my_secret }} like this   ",
        "Multiple inline substitutions ${{ ACTIONS.webhook.result }} and ${{ ACTIONS.webhook.url }}",
    ]
    expected_result = [
        {
            "test": {
                "data": "INLINE: 42",
                "url": "https://example.com",
                "number": 42,
                "number_whitespace": 42,
                "number_whitespace_multi_string": " 42 3  ",
            }
        },
        "Inline substitution 42 like this",
        "https://example.com",
        "            Again, inline substitution @@@ like this   ",
        "Multiple inline substitutions 42 and https://example.com",
    ]
    processed_templates = eval_templated_object(templates, operand=data)
    assert processed_templates == expected_result


def test_eval_templated_object_inline_fails_if_not_str():
    data = {
        "ACTIONS": {
            "webhook": {
                "result": 42,
                "url": "https://example.com",
                "count": 3,
            }
        },
        "SECRETS": {
            "my_secret": "@@@",
        },
    }
    with pytest.raises(TypeError) as e:
        _ = eval_templated_object(
            "${{ ACTIONS.webhook.result -> int }} ${{ ACTIONS.webhook.count }}",
            operand=data,
        )
    assert "expected str instance, int found" in str(e.value)
    with pytest.raises(TypeError) as e:
        _ = eval_templated_object(
            "   ${{ ACTIONS.webhook.result -> int }} ${{ ACTIONS.webhook.count }}   ",
            operand=data,
        )
    assert "expected str instance, int found" in str(e.value)
