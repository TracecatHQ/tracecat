import os
import uuid
from datetime import datetime, timedelta
from typing import Any, Literal

import httpx
import pytest
import respx
from httpx import Response
from pydantic import SecretStr

from tracecat import config
from tracecat.auth.types import Role
from tracecat.db.models import Secret
from tracecat.exceptions import TracecatExpressionError
from tracecat.expressions.common import (
    ExprContext,
    ExprType,
    IterableExpr,
    eval_jsonpath,
)
from tracecat.expressions.core import TemplateExpression
from tracecat.expressions.eval import (
    collect_expressions,
    eval_templated_object,
    extract_expressions,
    extract_templated_secrets,
)
from tracecat.expressions.parser.core import ExprParser
from tracecat.expressions.parser.evaluator import ExprEvaluator
from tracecat.expressions.patterns import STANDALONE_TEMPLATE
from tracecat.expressions.validator.validator import (
    ExpectedField,
    ExprValidationContext,
    ExprValidator,
    TemplateActionExprValidator,
    TemplateActionValidationContext,
)
from tracecat.logger import logger
from tracecat.secrets.constants import DEFAULT_SECRETS_ENVIRONMENT
from tracecat.secrets.encryption import decrypt_keyvalues, encrypt_keyvalues
from tracecat.secrets.enums import SecretType
from tracecat.secrets.schemas import SecretKeyValue, SecretRead
from tracecat.validation.common import get_validators
from tracecat.validation.schemas import (
    ExprBaseValidationResult,
    ValidationDetail,
)
from tracecat.variables.schemas import VariableCreate
from tracecat.variables.service import VariablesService


@pytest.fixture
def evaluator() -> ExprEvaluator:
    return ExprEvaluator()


@pytest.mark.parametrize(
    "base,indexes,expected",
    [
        (["hello", "world"], (1,), "world"),
        ((1, 2, 3), (0,), 1),
        ("tracecat", (-1,), "t"),
        (([["nested"]], 1), (0, 0, 0), "nested"),
        ({"foo": {"bar": 42}}, ("foo", "bar"), 42),
    ],
)
def test_primary_expr_indexing(
    evaluator: ExprEvaluator, base, indexes, expected
) -> None:
    assert evaluator.primary_expr(base, *indexes) == expected


@pytest.mark.parametrize(
    "base,indexes,error_message",
    [
        ([1, 2], (5,), "Sequence index 5 out of range"),
        ([1, 2], ("0",), "Sequence indices must be integers"),
        (42, (0,), "Object of type 'int' is not indexable"),
        ({"foo": 1}, ("bar",), "Key 'bar' not found for mapping access"),
    ],
)
def test_primary_expr_indexing_errors(
    evaluator: ExprEvaluator, base, indexes, error_message: str
) -> None:
    with pytest.raises(TracecatExpressionError) as exc:
        evaluator.primary_expr(base, *indexes)
    assert error_message in str(exc.value)


@pytest.mark.parametrize(
    "expression,expect_match",
    [
        ("${{ path.to.example -> asdf }}", True),
        ("${{ { hello: world } }}", True),
        ("${{ ${example} }}", True),
        ("${{ example ${var} }}", True),
        ("${{ path.to.example -> asdf }} ", False),
        ("${{ example }} more text", False),  # Not standalone
        ("${{ example }}${{ another }}", False),  # Should not match
        ("${{ example }} ${{ another }}", False),  # Should not match
        ("${{ ${{ hello }} }}", False),
        (
            "${{ inputs.metadata + [{'Status': FN.capitalize(FN.replace(inputs.status, '_', ' '))}] }}",
            True,
        ),
    ],
)
def test_standalone_template(expression, expect_match):
    matched = STANDALONE_TEMPLATE.match(expression) is not None
    assert matched == expect_match


def test_eval_jsonpath():
    operand = {"webhook": {"result": 42, "data": {"name": "John", "age": 30}}}
    assert eval_jsonpath("$.webhook.result", operand) == 42
    assert eval_jsonpath("$.webhook.data.name", operand) == "John"
    assert eval_jsonpath("$.webhook.data.age", operand) == 30
    with pytest.raises(TracecatExpressionError) as e:
        _ = eval_jsonpath("$.webhook.data.nonexistent", operand=operand, strict=True)
        assert "Operand has no path" in str(e.value)
    assert (
        eval_jsonpath("$.webhook.data.nonexistent", operand=operand, strict=False)
        is None
    )


@pytest.mark.parametrize(
    "expression,expected_result",
    [
        ("${{ACTIONS.webhook.result}}", 1),
        ("${{ ACTIONS.webhook.result -> int }}", 1),
        ("${{ TRIGGER.data.arg1 -> int }}", 1),
        ("${{ TRIGGER.data.arg1 }}", 1),  # Doesn't cast
        ("${{ TRIGGER.data.arg2 -> str }}", "2"),
        ("${{ ACTIONS.webhook.result -> str }}", "1"),
        ("${{ ACTIONS.path_A_first.result.path.nested.value -> int }}", 9999),
        (
            "${{ FN.add(TRIGGER.data.arg1, ACTIONS.path_A_first.result.path.nested.value) }}",
            10000,
        ),
    ],
)
def test_templated_expression_result(expression, expected_result):
    exec_vars = {
        ExprContext.TRIGGER: {
            "data": {
                "arg1": 1,
                "arg2": 2,
            },
        },
        ExprContext.ACTIONS: {
            "webhook": {"result": 1},
            "path_A_first": {"result": {"path": {"nested": {"value": 9999}}}},
            "path_A_second": {"result": 3},
            "path_B_first": {"result": 4},
            "path_B_second": {"result": 5},
        },
        "metadata": {"name": "John Doe", "age": 30},
    }

    fut = TemplateExpression(expression, operand=exec_vars)
    assert fut.result() == expected_result


@pytest.mark.parametrize(
    "expression,expected_result",
    [
        (
            "${{ FN.is_equal(bool(True), bool(1)) -> bool }}",
            True,
        ),
        (
            "${{ FN.add(int(1234), float(0.5)) -> float }}",
            1234.5,
        ),
        (
            "${{ FN.less_than(TRIGGER.data.arg1, TRIGGER.data.arg2) -> bool }}",
            True,
        ),
        (
            "${{ FN.is_equal(TRIGGER.data.arg1, ACTIONS.webhook.result) -> bool }}",
            True,
        ),
    ],
)
def test_templated_expression_function(expression, expected_result):
    exec_vars = {
        ExprContext.TRIGGER: {
            "data": {
                "arg1": 1,
                "arg2": 2,
            },
        },
        ExprContext.ACTIONS: {
            "webhook": {"result": 1},
            "path_A_first": {"result": {"path": {"nested": {"value": 9999}}}},
            "path_A_second": {"result": 3},
            "path_B_first": {"result": 4},
            "path_B_second": {"result": 5},
        },
        "metadata": {"name": "John Doe", "age": 30},
    }

    fut = TemplateExpression(expression, operand=exec_vars)
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


def test_find_secrets_in_complex_expression():
    # Should detect secret usages nested within a function call
    expr = '${{ FN.to_base64(SECRETS.zendesk.ZENDESK_EMAIL + "/token:" + SECRETS.zendesk.ZENDESK_API_TOKEN) }}'
    secrets = extract_templated_secrets(expr)
    assert sorted(secrets) == sorted(
        [
            "zendesk.ZENDESK_EMAIL",
            "zendesk.ZENDESK_API_TOKEN",
        ]
    )


# ------------------------------ New fixtures ------------------------------ #


@pytest.fixture
def simple_secret_expr():
    return "${{ SECRETS.alpha.KEY }}"


@pytest.fixture
def complex_secret_expr():
    return "${{ FN.join(':', [SECRETS.alpha.KEY, SECRETS.beta.SECRET, 'tail']) }}"


@pytest.fixture
def mixed_args_obj(simple_secret_expr, complex_secret_expr):
    return {
        "headers": {
            "Auth": complex_secret_expr,
            "Init": simple_secret_expr,
        },
        "payload": [
            "no templates here",
            "${{ 'prefix-' + SECRETS.gamma.TOKEN }}",
        ],
    }


# ------------------------------ Corner case tests ------------------------------ #


def test_evaluate_templated_secret(test_role: Role):
    TEST_SECRETS = {
        "my_secret": [
            SecretKeyValue(key="TEST_API_KEY_1", value=SecretStr("1234567890")),
            SecretKeyValue(key="NOISE_1", value=SecretStr("asdfasdf")),
        ],
        "other_secret": [
            SecretKeyValue(key="test_api_key_2", value=SecretStr("@@@@@@@@@")),
            SecretKeyValue(key="NOISE_2", value=SecretStr("aaaaaaaaaaaaa")),
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
                "c": "${{ SECRETS.my_secret.NOISE_1 }}",
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
                "c": "asdfasdf",
            },
            {
                "a": "3",
                "b": "4",
            },
        ],
    }

    base_secrets_url = f"{config.TRACECAT__API_URL}/secrets"

    def format_secrets_as_json(secrets: list[SecretRead]) -> dict[str, str]:
        """Format secrets as a dict."""
        secret_dict = {}
        for secret in secrets:
            secret_dict[secret.name] = {
                kv.key: kv.value.get_secret_value()
                for kv in decrypt_keyvalues(
                    secret.encrypted_keys, key=os.environ["TRACECAT__DB_ENCRYPTION_KEY"]
                )
            }
        return secret_dict

    def get_secret(secret_name: str) -> SecretRead:
        with httpx.Client(base_url=config.TRACECAT__API_URL) as client:
            response = client.get(f"/secrets/{secret_name}")
            response.raise_for_status()
        return SecretRead.model_validate(response.json())

    with respx.mock:
        # Mock workflow getter from API side
        for secret_name, secret_keys in TEST_SECRETS.items():
            secret = Secret(
                id=uuid.uuid4(),
                name=secret_name,
                workspace_id=uuid.uuid4(),
                # Explicitly set the concrete secret type so enums can be resolved during serialization.
                type=SecretType.CUSTOM.value,
                # Ensure the environment matches the default API environment to mimic production payloads.
                environment=DEFAULT_SECRETS_ENVIRONMENT,
                encrypted_keys=encrypt_keyvalues(
                    secret_keys, key=os.environ["TRACECAT__DB_ENCRYPTION_KEY"]
                ),
                created_at=datetime.now(),
                updated_at=datetime.now(),
                tags=None,
            )
            secret_payload = SecretRead.from_database(secret).model_dump(mode="json")

            # Mock hitting get secrets endpoint
            respx.get(f"{base_secrets_url}/{secret_name}").mock(
                return_value=Response(200, json=secret_payload)
            )

        # Start test
        secret_paths = extract_templated_secrets(mock_templated_kwargs)
        secret_names = [path.split(".")[0] for path in secret_paths]
        secrets = [get_secret(secret_name) for secret_name in secret_names]
        secret_ctx = {ExprContext.SECRETS: format_secrets_as_json(secrets)}
        actual = eval_templated_object(obj=mock_templated_kwargs, operand=secret_ctx)
    assert actual == exptected


def test_eval_templated_object():
    data = {
        ExprContext.ACTIONS: {
            "webhook": {
                "result": 42,
                "url": "https://example.com",
                "count": 3,
            }
        },
        ExprContext.SECRETS: {
            "my_secret": "@@@",
        },
        ExprContext.LOCAL_VARS: {
            "x": 5,
            "y": "100",
        },
    }
    templates = [
        {
            "test": {
                "data": "INLINE: ${{ ACTIONS.webhook.result -> str }}",
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
        " ignore local vars ${{ var.y }}",
        "${{ var.x }}",
        "${{ FN.add(5, var.x) }}",
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
        " ignore local vars 100",
        5,
        10,
    ]
    processed_templates = eval_templated_object(templates, operand=data)
    assert processed_templates == expected_result


def test_eval_templated_object_inline_fails_if_not_str():
    data = {
        ExprContext.ACTIONS: {
            "webhook": {
                "result": 42,
                "url": "https://example.com",
                "count": 3,
            }
        },
        ExprContext.SECRETS: {
            "my_secret": "@@@",
        },
    }
    actual1 = eval_templated_object(
        "${{ ACTIONS.webhook.result -> int }} ${{ ACTIONS.webhook.count }}",
        operand=data,
    )
    assert actual1 == "42 3"
    actual2 = eval_templated_object(
        "   ${{ ACTIONS.webhook.result -> int }} ${{ ACTIONS.webhook.count }}   ",
        operand=data,
    )
    assert actual2 == "   42 3   "


@pytest.mark.parametrize(
    "expr, expected",
    [
        # Action expressions
        ("ACTIONS.action_test.bar -> str", "1"),
        ("str(ACTIONS.action_test.bar)", "1"),
        ("ACTIONS.action_test.bar", 1),
        ("       ACTIONS.action_test.baz    ", 2),
        ("ACTIONS.action_test", {"bar": 1, "baz": 2}),
        ("   ACTIONS.action_test", {"bar": 1, "baz": 2}),
        ("VARS.api_config.base_url", "https://example.com"),
        ("VARS.api_config.timeout", 30),
        # Secret expressions
        ("SECRETS.secret_test.KEY", "SECRET"),
        ("   SECRETS.secret_test.KEY    ", "SECRET"),
        # Function expressions
        ("FN.concat(ENV.item, '5')", "ITEM5"),
        ("FN.add(5, 2)", 7),
        ("  FN.is_null(None)   ", True),
        ("FN.contains('a', TRIGGER.data.my.module.items)", True),
        ("FN.length([1, 2, 3])", 3),
        ("FN.join(['A', 'B', 'C'], ',')", "A,B,C"),
        ("FN.join(['A', 'B', 'C'], '@')", "A@B@C"),
        ("FN.contains('A', ['A', 'B', 'C'])", True),
        ("FN.format('Formatted: {} !', 'yay')", "Formatted: yay !"),
        (
            "FN.format.map('Hey {}!', ['Alice', 'Bob', 'Charlie'])",
            ["Hey Alice!", "Hey Bob!", "Hey Charlie!"],
        ),
        (
            "FN.format.map('Hello, {}! You are {}.', ['Alice', 'Bob', 'Charlie'], TRIGGER.data.adjectives)",
            [
                "Hello, Alice! You are cool.",
                "Hello, Bob! You are awesome.",
                "Hello, Charlie! You are happy.",
            ],
        ),
        (
            "FN.flatten([[1, 2, 3], [4, 5, 6], [7, 8, 9]])",
            [1, 2, 3, 4, 5, 6, 7, 8, 9],
        ),
        # Ternary expressions
        (
            "'It contains 1' if FN.contains(1, TRIGGER.data.list) else 'it does not contain 1'",
            "It contains 1",
        ),
        ("True if FN.contains('key1', TRIGGER.data.dict) else False", True),
        ("True if FN.contains('key2', TRIGGER.data.dict) else False", False),
        ("True if FN.does_not_contain('key2', TRIGGER.data.dict) else False", True),
        ("True if FN.does_not_contain('key1', TRIGGER.data.dict) else False", False),
        (
            "None if FN.does_not_contain('key1', TRIGGER.data.dict) else TRIGGER.data.dict.key1",
            1,
        ),
        (
            "'ok' if TRIGGER.hits2._source.data_stream.namespace else TRIGGER.hits2._source.data_stream.namespace",
            "ok",
        ),
        (
            "None if TRIGGER.hits2._source.data_stream.namespace == None else TRIGGER.hits2._source.data_stream.namespace",
            "_NAMESPACE",
        ),
        ("True if TRIGGER.hits2._source.host.ip else False", False),
        # Truthy expressions
        ("TRIGGER.hits2._source.host.ip", None),
        ("TRIGGER.data.people[4].name", None),
        # Typecast expressions
        ("int(5)", 5),
        ("float(5.0)", 5.0),
        ("str('hello')", "hello"),
        # Literals
        ("'hello'", "hello"),
        ("True", True),
        ("False", False),
        ("None", None),
        ("5", 5),
        ("5.0", 5.0),
        ("5000", 5000),
        ("'500'", "500"),
        ("bool(True)", True),
        ("bool(1)", True),
        ("str(1)", "1"),
        ("[1, 2, 3]", [1, 2, 3]),
        # Environment expressions
        ("ENV.item", "ITEM"),
        ("ENV.var", "VAR"),
        # Trigger expressions
        ("TRIGGER.data.name", "John"),
        ("TRIGGER.data.age", 30),
        ("TRIGGER.value -> int", 100),
        ## Try more uncommon trigger expressions
        ("TRIGGER.hits._test", "_test_ok"),
        ("TRIGGER.hits.________test", "________test_ok"),
        ("TRIGGER.hits._source['kibana.alert.rule.name']", "TEST"),
        ("TRIGGER.hits._source.['kibana.alert.rule.name']", "TEST"),
        ('TRIGGER.hits._source.["kibana.alert.rule.name"]', "TEST"),
        ('TRIGGER.hits._source."kibana.alert.rule.name"', "TEST"),
        ("TRIGGER.hits._source.'kibana.alert.rule.name'", "TEST"),
        ("TRIGGER.hits.'_source'.['kibana.alert.rule.name']", "TEST"),
        ("TRIGGER.hits.'_source'.['kibana.alert.rule.name']", "TEST"),
        ("TRIGGER.hits.['_source']['kibana.alert.rule.name']", "TEST"),
        # Local variables
        ("var.x", 5),
        ("var.y", "100"),
        ("var.y -> int", 100),
        # Test jsonpath
        ("TRIGGER.data.people[1].name", "Bob"),
        ("TRIGGER.data.people[2].age -> str", "50"),
        ("TRIGGER.data.people[*].age", [30, 40, 50]),
        ("TRIGGER.data.people[*].name", ["Alice", "Bob", "Charlie"]),
        ("TRIGGER.data.people[*].gender", ["female", "male"]),
        # ('TRIGGER.data.["user@tracecat.com"].name', "Bob"), TODO: Add support for object key indexing
        # Test direct indexing expressions
        ## List indexing
        ("ACTIONS.test_list[0]", "hello"),
        ("ACTIONS.test_list[1]", "world"),
        ("ACTIONS.test_list[3]", "bar"),
        ("ACTIONS.test_list[-1]", "bar"),
        ("ACTIONS.test_list[-2]", "foo"),
        ## String indexing
        ("ACTIONS.test_string[0]", "t"),
        ("ACTIONS.test_string[-1]", "t"),
        ("ACTIONS.test_string[5]", "c"),
        ## Tuple indexing
        ("ACTIONS.test_tuple[0]", 10),
        ("ACTIONS.test_tuple[1]", 20),
        ("ACTIONS.test_tuple[2]", 30),
        ("ACTIONS.test_tuple[-1]", 30),
        ## Nested indexing
        ("ACTIONS.nested_structure['level1']['level2'][0]['name']", "nested_item"),
        ("ACTIONS.nested_structure['level1']['level2'][0]['value']", 99),
        ("ACTIONS.mixed_nested[0][0][0]", "deep"),
        ("ACTIONS.mixed_nested[1][0][0]", "value"),
        ## Mixed dot and bracket notation
        ("ACTIONS.nested_structure.level1.level2[0].name", "nested_item"),
        ("ACTIONS.nested_structure.level1['level2'][0]['value']", 99),
        ## List indexing with existing data
        ("TRIGGER.data.list[0]", 1),
        ("TRIGGER.data.list[1]", 2),
        ("TRIGGER.data.list[-1]", 3),
        ("TRIGGER.data.adjectives[0]", "cool"),
        ("TRIGGER.data.adjectives[2]", "happy"),
        ("TRIGGER.data.my.module.items[0]", "a"),
        ("TRIGGER.data.my.module.items[1]", "b"),
        ("TRIGGER.data.my.module.items[-1]", "c"),
        ## Function result indexing
        ("FN.split('hello,world,foo', ',')[0]", "hello"),
        ("FN.split('hello,world,foo', ',')[1]", "world"),
        ("FN.split('hello,world,foo', ',')[-1]", "foo"),
        ("FN.split(TRIGGER.data.text, 'e')[0]", "t"),
        ("FN.split(TRIGGER.data.text, 'e')[1]", "st"),
        # Combination
        ("'a' if FN.is_equal(var.y, '100') else 'b'", "a"),
        ("'a' if var.y == '100' else 'b'", "a"),
        ("('a' if var.y != '100' else ('b' if var.y == '200' else 'c'))", "c"),
        # Control flow
        ("for var.jsonpath in [1,2,3]", IterableExpr(".jsonpath", [1, 2, 3])),
        # More
        ("FN.sum([1, FN.sub(2, 3), 1 - 1])", 0),
        ("FN.sum([1,2,3,4,5]) + 10", 25),
        ("'a' + 'b' == 'ab'", True),
        ("FN.sum([1,2,3]) -> int", 6),
        ("[1,2,3] + [4,5,6]", [1, 2, 3, 4, 5, 6]),
        ("'hello' if False else 'goodbye'", "goodbye"),
        ("{ 'key1': 1, 'key2': 'value' }", {"key1": 1, "key2": "value"}),
        ("(1 + 10) > 3 -> str", "True"),
        ("True || (1 != 1)", True),
        # Advanced jsonpath
        ## Filtering
        ("TRIGGER.data..name", ["John", "Alice", "Bob", "Charlie", "Bob"]),
        ("ACTIONS.users[?active == true].name", ["Alice", "Charlie"]),
        (
            "ACTIONS.users[?age >= 40]",
            [
                {
                    "name": "Bob",
                    "age": 40,
                    "gender": "male",
                    "active": False,
                    "contact": {
                        "email": "bob@example.com",
                        "phone": "098-765-4321",
                    },
                },
                {
                    "name": "Charlie",
                    "age": 50,
                    "gender": "male",
                    "active": True,
                    "contact": {
                        "email": "charlie@example.com",
                        "phone": "111-222-3333",
                    },
                },
            ],
        ),
        ("ACTIONS.users[?gender == 'female'].name", "Alice"),
        (
            "ACTIONS.users[?age >= 30 & age <= 40].name",
            ["Alice", "Bob"],
        ),
        ## Substituting
        (
            "ACTIONS.users[?gender == 'male'].contact.email.`sub(/example.com/, example.net)`",
            ["bob@example.net", "charlie@example.net"],
        ),
        ("ACTIONS.empty[0].index", None),
        ("ACTIONS.null_value.result.result", None),
    ],
)
def test_expression_parser(expr, expected):
    context = {
        ExprContext.ACTIONS: {
            "action_test": {
                "bar": 1,
                "baz": 2,
            },
            "test_list": ["hello", "world", "foo", "bar"],
            "test_string": "tracecat",
            "test_tuple": (10, 20, 30),
            "nested_structure": {
                "level1": {"level2": [{"name": "nested_item", "value": 99}]}
            },
            "mixed_nested": [[["deep"]], [["value"]]],
            "users": [
                {
                    "name": "Alice",
                    "age": 30,
                    "gender": "female",
                    "active": True,
                    "contact": {
                        "email": "alice@example.com",
                        "phone": "123-456-7890",
                    },
                },
                {
                    "name": "Bob",
                    "age": 40,
                    "gender": "male",
                    "active": False,
                    "contact": {
                        "email": "bob@example.com",
                        "phone": "098-765-4321",
                    },
                },
                {
                    "name": "Charlie",
                    "age": 50,
                    "gender": "male",
                    "active": True,
                    "contact": {
                        "email": "charlie@example.com",
                        "phone": "111-222-3333",
                    },
                },
            ],
            "empty": [],
            "null_value": None,
        },
        ExprContext.SECRETS: {
            "secret_test": {
                "KEY": "SECRET",
            },
        },
        ExprContext.VARS: {
            "api_config": {
                "base_url": "https://example.com",
                "timeout": 30,
            }
        },
        ExprContext.ENV: {
            "item": "ITEM",
            "var": "VAR",
        },
        ExprContext.TRIGGER: {
            "data": {
                "name": "John",
                "age": 30,
                "list": [1, 2, 3],
                "dict": {
                    "key1": 1,
                },
                "my": {
                    "module": {
                        "items": ["a", "b", "c"],
                    },
                },
                "adjectives": ["cool", "awesome", "happy"],
                "people": [
                    {
                        "name": "Alice",
                        "age": 30,
                        "gender": "female",
                    },
                    {
                        "name": "Bob",
                        "age": 40,
                        "gender": "male",
                    },
                    {
                        "name": "Charlie",
                        "age": 50,
                    },
                ],
                "user@tracecat.com": {
                    "name": "Bob",
                },
                "numbers": [1, 2, 3],
                "text": "test",
            },
            "value": "100",
            "hits": {
                # Leading underscore + flattened key
                "_source": {
                    "kibana.alert.rule.name": "TEST",
                },
                "_test": "_test_ok",
                "________test": "________test_ok",
            },
            "hits2": {
                "_id": "ID",
                "_index": ".internal.alerts-security.alerts-default-000007",
                "_score": 0,
                "_source": {
                    "@timestamp": "2024-08-15T13:45:39.808Z",
                    "agent": {
                        "ephemeral_id": "_agent_ephemeral_id",
                        "id": "_agent_id",
                        "name": "_name",
                        "type": "filebeat",
                        "version": "8.13.4",
                    },
                    "data_stream": {
                        "dataset": "system.security",
                        "namespace": "_NAMESPACE",
                        "type": "logs",
                    },
                },
            },
        },
        ExprContext.LOCAL_VARS: {
            "x": 5,
            "y": "100",
        },
    }
    # visitor = ExprEvaluatorVisitor(context=context)
    # parser = ExpressionParser()
    # assert parser.walk_expr(expr, visitor) == expected
    parser = ExprParser()
    parse_tree = parser.parse(expr)
    # ExprContext is a StrEnum so values are strings - pyright doesn't recognize this
    ev = ExprEvaluator(operand=context)  # pyright: ignore[reportArgumentType]
    assert parse_tree is not None
    actual = ev.transform(parse_tree)
    assert actual == expected


@pytest.mark.parametrize(
    "lhs, rhs, condition, expected",
    [
        ## Truthy cases
        # Basic comparison operators
        (85, 75, ">", True),  # Greater than
        (60, 60, ">=", True),  # Greater than or equal
        (30, 50, "<", True),  # Less than
        (200, 200, "==", True),  # Equal
        (403, 200, "!=", True),  # Not equal
        # Membership operators
        ("192.168.1.1", ["192.168.1.1", "10.0.0.1"], "in", True),  # In list
        ("8.8.8.8", ["192.168.1.1", "10.0.0.1"], "not in", True),  # Not in list
        # Identity operators
        (None, None, "is", True),  # Is same object
        (None, "not_none", "is not", True),  # Is not same object
        ## Falsy cases
        # Basic comparison operators
        (75, 85, ">", False),  # Greater than
        (50, 60, ">=", False),  # Greater than or equal
        (50, 30, "<", False),  # Less than
        (40, 30, "<=", False),  # Less than or equal
        (404, 200, "==", False),  # Equal
        (200, 200, "!=", False),  # Not equal
        # Membership operators
        ("192.168.1.1", ["10.0.0.1", "172.16.0.1"], "in", False),  # In list
        ("192.168.1.1", ["192.168.1.1", "10.0.0.1"], "not in", False),  # Not in list
        # Identity operators
        ("running", None, "is", False),  # Is same object
        (None, None, "is not", False),  # Is not same object
    ],
)
def test_expression_binary_ops(lhs, rhs, condition, expected):
    """Tests all binary operators:
    - ==
    - !=
    - >
    - >=
    - <
    - <=
    - in
    - not in
    - is
    - is not
    """
    expr = f"${{{{ {lhs!r} {condition} {rhs!r} }}}}"
    assert eval_templated_object(expr) == expected


def test_jsonpath_wildcard():
    context = {
        ExprContext.ACTIONS: {
            "users": [
                {"name": "Alice", "age": 30},
                {"name": "Bob", "age": 40},
                {"name": "Charlie", "age": 50},
            ],
            "companies": [
                {
                    "name": [
                        {"value": 1},
                    ],
                },
            ],
        },
    }
    # Wildcard, multiple matches
    # This should return a list
    expr = "ACTIONS.users[*].name"
    parser = ExprParser()
    parse_tree = parser.parse(expr)
    assert parse_tree is not None
    # ExprContext is a StrEnum so values are strings - pyright doesn't recognize this
    ev = ExprEvaluator(operand=context)  # pyright: ignore[reportArgumentType]
    actual = ev.transform(parse_tree)
    assert actual == ["Alice", "Bob", "Charlie"]

    # Single match, explicit index
    # This should return a single value
    expr = "ACTIONS.users[0].name"
    parser = ExprParser()
    parse_tree = parser.parse(expr)
    assert parse_tree is not None
    ev = ExprEvaluator(operand=context)  # pyright: ignore[reportArgumentType]
    actual = ev.transform(parse_tree)
    assert actual == "Alice"

    # Single match, wildcard
    # This should return a list
    expr = "ACTIONS.companies[*].name"
    parser = ExprParser()
    parse_tree = parser.parse(expr)
    assert parse_tree is not None
    ev = ExprEvaluator(operand=context)  # pyright: ignore[reportArgumentType]
    actual = ev.transform(parse_tree)
    # Returns a single list of all the values
    assert actual == [[{"value": 1}]]

    # Chained wildcard
    expr = "ACTIONS.companies[*].name[*].value"
    parser = ExprParser()
    parse_tree = parser.parse(expr)
    assert parse_tree is not None
    ev = ExprEvaluator(operand=context)  # pyright: ignore[reportArgumentType]
    actual = ev.transform(parse_tree)
    assert actual == [1]


def test_jsonpath_filter_returns_list():
    context = {
        ExprContext.ACTIONS: {
            "parse_event": {
                "result": {
                    "included": [
                        {
                            "attributes": {
                                "incident_role": {
                                    "data": {"attributes": {"slug": "primary-role"}}
                                }
                            }
                        },
                        {
                            "attributes": {
                                "incident_role": {
                                    "data": {"attributes": {"slug": "secondary-role"}}
                                }
                            }
                        },
                    ]
                }
            }
        }
    }

    expr = 'ACTIONS.parse_event.result.included[?(@.attributes.incident_role.data.attributes.slug != "primary-role")].attributes.incident_role.data.attributes.slug'
    parser = ExprParser()
    parse_tree = parser.parse(expr)
    assert parse_tree is not None
    ev = ExprEvaluator(operand=context)  # pyright: ignore[reportArgumentType]
    actual = ev.transform(parse_tree)
    assert actual == ["secondary-role"]

    expr = "ACTIONS.parse_event.result.included[?(@.attributes.incident_role.data.attributes.slug)].attributes.incident_role.data.attributes.slug"
    parse_tree = parser.parse(expr)
    assert parse_tree is not None
    actual = ev.transform(parse_tree)
    assert actual == [
        "primary-role",
        "secondary-role",
    ]


def test_time_funcs():
    time_now_expr = "${{ FN.now() }}"
    dt = eval_templated_object(time_now_expr)
    logger.info(dt)
    assert isinstance(dt, datetime)

    time_now_expr = "${{ FN.minutes(5) }}"
    td = eval_templated_object(time_now_expr)
    logger.info(td)
    assert isinstance(td, timedelta)

    mins_ago_expr = "${{ FN.now() - FN.minutes(5) }}"
    dt = eval_templated_object(mins_ago_expr)
    logger.info(dt)
    assert isinstance(dt, datetime)

    context = {
        ExprContext.ENV: {
            "workflow": {
                "start_time": datetime(2024, 1, 1, 0, 0, 0),
            }
        }
    }

    time_now_expr = "${{ ENV.workflow.start_time + FN.minutes(5) }}"
    dt = eval_templated_object(time_now_expr, operand=context)
    logger.info(dt)
    assert isinstance(dt, datetime)
    assert dt == datetime(2024, 1, 1, 0, 5, 0)


def test_parser_error():
    context = {
        ExprContext.ACTIONS: {
            "action_test": {
                "bar": 1,
                "baz": 2,
            },
        },
    }

    expr = "ACTIONS.action_test.bar -> str -> int"
    parser = ExprParser()
    with pytest.raises(TracecatExpressionError):
        parser.parse(expr)

    # ExprContext is a StrEnum so values are strings - pyright doesn't recognize this
    strict_evaluator = ExprEvaluator(operand=context, strict=True)  # pyright: ignore[reportArgumentType]
    with pytest.raises(TracecatExpressionError):
        test = "ACTIONS.action_test.foo"
        parse_tree = parser.parse(test)
        assert parse_tree is not None
        strict_evaluator.evaluate(parse_tree)

    evaluator = ExprEvaluator(operand=context, strict=False)  # pyright: ignore[reportArgumentType]
    test = "ACTIONS.action_test.foo.bar.baz"
    parse_tree = parser.parse(test)
    assert parse_tree is not None
    assert evaluator.evaluate(parse_tree) is None


def assert_validation_result(
    res: ExprBaseValidationResult,
    *,
    type: ExprType,
    status: Literal["success", "error"],
    contains_msg: str | None = None,
    contains_detail: str | None = None,
):
    assert res.expression_type == type, (
        f"Expected {type}, got {res.expression_type}. {res}"
    )
    assert res.status == status, f"Expected {status}, got {res.status}. {res.msg}"
    if contains_msg:
        assert contains_msg in res.msg
    if contains_detail:
        assert res.detail is not None
        assert contains_detail in res.detail


def assert_validation_detail(
    res: ValidationDetail,
    *,
    type: ExprType,
    contains_msg: str | None = None,
    **kwargs: Any,
):
    assert res.type == type, f"Expected {type}, got {res.type}. {res}"
    if contains_msg:
        assert contains_msg in res.msg


@pytest.mark.parametrize(
    "expr,expected",
    [
        (
            "${{ int(FN.fail(5, var.x)) }}",
            [{"type": ExprType.FUNCTION, "status": "error", "contains_msg": "'fail'"}],
        ),
        (
            "${{ FN.fail_again.map(5, var.x) }}",
            [
                {
                    "type": ExprType.FUNCTION,
                    "status": "error",
                    "contains_msg": "'fail_again'",
                }
            ],
        ),
        (
            "${{ false }}",
            [{"type": ExprType.GENERIC, "status": "error"}],
        ),
        (
            "${{ SECRETS.xxxxxxxxxxxxxxxxxxxxx.WORLD }}",
            [
                {
                    "type": ExprType.SECRET,
                    "status": "error",
                    "contains_msg": "'xxxxxxxxxxxxxxxxxxxxx'",
                }
            ],
        ),
        (
            "${{ ACTIONS.my_action.url.result }}",
            [{"type": ExprType.ACTION, "status": "error"}],
        ),
        (
            "${{ ACTIONS.random.result -> int }}",
            [{"type": ExprType.ACTION, "status": "error"}],
        ),
        (
            "${{ ACTIONS.my_action.result }} ${{ ACTIONS.my_action.count }}  ",
            [
                {"type": ExprType.ACTION, "status": "error", "contains_msg": "'count'"},
            ],
        ),
        (
            "${{ ACTIONS.my_action.url }}",
            [{"type": ExprType.ACTION, "status": "error"}],
        ),
        (
            "Again, inline substitution ${{ SECRETS.my_secret }} like this   ",
            [
                {
                    "type": ExprType.SECRET,
                    "status": "error",
                    "contains_msg": "my_secret",
                }
            ],
        ),
        (
            "Multiple inline substitutions ${{ ACTIONS.my_action.result }} and ${{ ACTIONS.my_action.url }} "
            "no errors ${{ 'hello' }} another error ${{ SECRETS.some_secret }}",
            [
                {"type": ExprType.ACTION, "status": "error", "contains_msg": "'url'"},
                {
                    "type": ExprType.SECRET,
                    "status": "error",
                    "contains_msg": "some_secret",
                },
            ],
        ),
        (
            {
                "test": {
                    "data": "INLINE: ${{ int('fails') }}",
                    "url": "${{ int(100) }}",
                },
                "test2": "fail 1 ${{ ACTIONS.my_action.invalid }} ",
            },
            [
                {
                    "type": ExprType.ACTION,
                    "status": "error",
                    "contains_msg": "invalid",
                },
                {
                    "type": ExprType.TYPECAST,
                    "status": "error",
                    "contains_msg": "fails",
                },
            ],
        ),
        (
            {
                "test": {
                    "data": "INLINE: ${{ FN.add() -> invalid }}",
                },
            },
            [
                {
                    "type": ExprType.GENERIC,
                    "status": "error",
                    "contains_msg": "invalid",
                },
            ],
        ),
    ],
)
@pytest.mark.anyio
async def test_extract_expressions_errors(expr, expected, test_role, env_sandbox):
    # The only defined action reference is "my_action"
    validation_context = ExprValidationContext(
        action_refs={"my_action"},
    )
    validators = get_validators()

    async with ExprValidator(
        validation_context=validation_context,
        validators=validators,
    ) as visitor:
        exprs = extract_expressions(expr)
        for _expr in exprs:
            # This queues up all the coros in the taskgroup
            # and executes them concurrently on exit
            _expr.validate(visitor)

    errors = sorted(set(visitor.errors()), key=lambda x: x.type)

    for actual, ex in zip(errors, expected, strict=True):
        assert_validation_detail(actual, **ex)


@pytest.mark.anyio
async def test_expr_validator_vars_key_depth_limit(test_role, env_sandbox):
    async with VariablesService.with_session(role=test_role) as service:
        await service.create_variable(
            VariableCreate(
                name="config",
                values={
                    "base_url": "https://example.com",
                    "api": {
                        "base_url": "https://example.com/api",
                    },
                },
            )
        )

    validation_context = ExprValidationContext(action_refs=set())

    async with ExprValidator(
        validation_context=validation_context
    ) as success_validator:
        for template in extract_expressions("${{ VARS.config.base_url }}"):
            template.validate(success_validator)
    assert success_validator.errors() == []

    async with ExprValidator(validation_context=validation_context) as depth_validator:
        for template in extract_expressions("${{ VARS.config.api.base_url }}"):
            template.validate(depth_validator)
    depth_errors = depth_validator.errors()
    assert len(depth_errors) == 1
    depth_error = depth_errors[0]
    assert depth_error.type == ExprType.VARIABLE
    assert "support at most one key segment" in depth_error.msg

    parser = ExprParser()
    evaluator = ExprEvaluator(
        operand={ExprContext.VARS: {"config": {"api": {"base_url": "value"}}}},
        strict=True,
    )
    parse_tree = parser.parse("VARS.config.api.base_url")
    assert parse_tree is not None
    with pytest.raises(
        TracecatExpressionError, match="support at most one key segment"
    ):
        evaluator.evaluate(parse_tree)


@pytest.mark.parametrize(
    "context,expr,expected",
    [
        ({"TRIGGER": {"data": {"foo": "bar"}}}, "TRIGGER", {"data": {"foo": "bar"}}),
        ({"TRIGGER": "data"}, "TRIGGER", "data"),
        ({"TRIGGER": None}, "TRIGGER", None),
        ({"TRIGGER": [1, 2, 3]}, "TRIGGER", [1, 2, 3]),
    ],
)
def test_parse_trigger_json(context, expr, expected):
    parser = ExprParser()
    parse_tree = parser.parse(expr)
    ev = ExprEvaluator(operand=context)
    assert parse_tree is not None
    actual = ev.transform(parse_tree)
    assert actual == expected


@pytest.mark.parametrize(
    "expr,expected",
    [
        # Test valid template action input references
        (
            "${{ inputs.my_input }}",
            [{"type": ExprType.TEMPLATE_ACTION_INPUT, "status": "success"}],
        ),
        (
            "${{ inputs.my_input.nested }}",
            [{"type": ExprType.TEMPLATE_ACTION_INPUT, "status": "success"}],
        ),
        # Test invalid template action input references
        (
            "${{ inputs.invalid_input }}",
            [
                {
                    "type": ExprType.TEMPLATE_ACTION_INPUT,
                    "status": "error",
                    "contains_msg": "Invalid input reference 'invalid_input'. Valid inputs are: ['my_input', 'other_input']",
                }
            ],
        ),
        # Test valid template action step references
        (
            "${{ steps.step1.result }}",
            [{"type": ExprType.TEMPLATE_ACTION_STEP, "status": "success"}],
        ),
        (
            "${{ steps.step2.output }}",
            [{"type": ExprType.TEMPLATE_ACTION_STEP, "status": "success"}],
        ),
        # Test invalid template action step references
        (
            "${{ steps.invalid_step.result }}",
            [
                {
                    "type": ExprType.TEMPLATE_ACTION_STEP,
                    "status": "error",
                    "contains_msg": "Invalid step reference 'invalid_step'. Valid steps are: ['step1', 'step2']",
                }
            ],
        ),
        # Test multiple expressions
        (
            {
                "input": "${{ inputs.my_input }}",
                "step": "${{ steps.step1.result }}",
                "invalid": "${{ steps.bad_step.result }}",
            },
            [
                {"type": ExprType.TEMPLATE_ACTION_INPUT, "status": "success"},
                {"type": ExprType.TEMPLATE_ACTION_STEP, "status": "success"},
                {
                    "type": ExprType.TEMPLATE_ACTION_STEP,
                    "status": "error",
                    "contains_msg": "Invalid step reference 'bad_step'. Valid steps are: ['step1', 'step2']",
                },
            ],
        ),
    ],
)
@pytest.mark.anyio
async def test_template_action_validator(expr, expected):
    """Test validation of template action expressions."""
    # Set up validation context with expected inputs and valid step references
    validation_context = TemplateActionValidationContext(
        expects={
            "my_input": ExpectedField(type="str"),
            "other_input": ExpectedField(type="int"),
        },
        step_refs={"step1", "step2"},
    )

    visitor = TemplateActionExprValidator(
        validation_context=validation_context,
    )
    exprs = extract_expressions(expr)
    for _expr in exprs:
        _expr.validate(visitor)

    errors = list(visitor.results())

    for actual, ex in zip(errors, expected, strict=True):
        assert_validation_result(actual, **ex)


@pytest.mark.parametrize(
    "expr,expected_error",
    [
        # Test that ACTION expressions are not supported
        (
            "${{ ACTIONS.some_action.result }}",
            {
                "type": ExprType.ACTION,
                "status": "error",
                "contains_msg": "ACTIONS expressions are not supported in Template Actions",
            },
        ),
        # Test that TRIGGER expressions are not supported
        (
            "${{ TRIGGER.some_data }}",
            {
                "type": ExprType.TRIGGER,
                "status": "error",
                "contains_msg": "TRIGGER expressions are not supported in Template Actions",
            },
        ),
        # Test that ENV expressions are not supported
        (
            "${{ ENV.some_var }}",
            {
                "type": ExprType.ENV,
                "status": "error",
                "contains_msg": "ENV expressions are not supported in Template Actions",
            },
        ),
        # Test that local var expressions are not supported
        (
            "${{ var.some_var }}",
            {
                "type": ExprType.LOCAL_VARS,
                "status": "error",
                "contains_msg": "var expressions are not supported in Template Actions",
            },
        ),
        # Test that iterator expressions are not supported
        (
            "${{ for var.item in [1,2,3] }}",
            {
                "type": ExprType.ITERATOR,
                "status": "error",
                "contains_msg": "for_each expressions are not supported in Template Actions",
            },
        ),
    ],
)
@pytest.mark.anyio
async def test_template_action_validator_unsupported_expressions(expr, expected_error):
    """Test validation of unsupported expressions in template actions."""
    validation_context = TemplateActionValidationContext(expects={}, step_refs=set())

    visitor = TemplateActionExprValidator(
        validation_context=validation_context,
    )
    exprs = extract_expressions(expr)
    for _expr in exprs:
        _expr.validate(visitor)

    val_results = list(visitor.results())

    # Expect that in the validation results, the expected error is present
    found_err = next(
        (
            r
            for r in val_results
            if (
                r.expression_type == expected_error["type"]
                and r.status == expected_error["status"]
                and expected_error["contains_msg"] in r.msg
            )
        ),
        None,
    )

    assert found_err is not None, f"Expected {expected_error}, got {val_results}"


@pytest.mark.parametrize(
    "test_name,data,template,expected",
    [
        (
            "basic_template_key",
            {
                ExprContext.ACTIONS: {
                    "input_action": {
                        "result": {"key_name": "dynamic_key", "value": "dynamic_value"}
                    }
                }
            },
            {
                "${{ ACTIONS.input_action.result.key_name }}": "${{ ACTIONS.input_action.result.value }}",
                "static_key": {
                    "${{ ACTIONS.input_action.result.key_name }}_nested": "${{ ACTIONS.input_action.result.value }}"
                },
            },
            {
                "dynamic_key": "dynamic_value",
                "static_key": {"dynamic_key_nested": "dynamic_value"},
            },
        ),
        (
            "multiple_expressions_in_key",
            {
                ExprContext.ACTIONS: {
                    "prefix_action": {"result": "test"},
                    "suffix_action": {"result": "key"},
                    "value_action": {"result": "result"},
                }
            },
            {
                "${{ ACTIONS.prefix_action.result }}_${{ ACTIONS.suffix_action.result }}": "${{ ACTIONS.value_action.result }}"
            },
            {"test_key": "result"},
        ),
        (
            "non_string_key_evaluation",
            {
                ExprContext.ACTIONS: {
                    "key_action": {"result": 123},
                    "value_action": {"result": "numeric_key"},
                }
            },
            {"${{ ACTIONS.key_action.result }}": "${{ ACTIONS.value_action.result }}"},
            {123: "numeric_key"},
        ),
        (
            "deeply_nested_key_expressions",
            {
                ExprContext.ACTIONS: {
                    "level1_action": {"result": "outer"},
                    "level2_action": {"result": "inner"},
                    "value_action": {"result": "nested_value"},
                }
            },
            {
                "${{ ACTIONS.level1_action.result }}": {
                    "${{ ACTIONS.level2_action.result }}": {
                        "${{ ACTIONS.level1_action.result }}_${{ ACTIONS.level2_action.result }}": "${{ ACTIONS.value_action.result }}"
                    }
                }
            },
            {"outer": {"inner": {"outer_inner": "nested_value"}}},
        ),
    ],
    ids=lambda x: x if isinstance(x, str) else "data",
)
def test_eval_templated_object_with_key_expressions(
    test_name, data, template, expected
):
    """Test that expressions in dictionary keys are properly evaluated."""
    result = eval_templated_object(template, operand=data)
    assert result == expected


@pytest.mark.anyio
@pytest.mark.parametrize(
    "expr,expected",
    [
        # Test valid workflow action keys
        (
            {
                "${{ ACTIONS.my_action.result.key }}": "value",
            },
            [{"type": ExprType.ACTION, "status": "success"}],
        ),
        # Test invalid workflow action reference
        (
            {"${{ ACTIONS.invalid_action.result }}": "value"},
            [
                {
                    "type": ExprType.ACTION,
                    "status": "error",
                    "contains_msg": "Invalid action reference",
                },
                {
                    "type": ExprType.ACTION,
                    "status": "success",  # The validator also returns a success result for the full path
                },
            ],
        ),
        # Test nested expressions in keys
        (
            {"parent": {"${{ ACTIONS.my_action.result }}": "value"}},
            [{"type": ExprType.ACTION, "status": "success"}],
        ),
    ],
)
async def test_validate_workflow_key_expressions(expr, expected):
    """Test validation of expressions in workflow dictionary keys."""
    validation_context = ExprValidationContext(
        action_refs={"my_action"},  # Only my_action is valid
    )
    validators = get_validators()

    async with ExprValidator(
        validation_context=validation_context,
        validators=validators,
        keep_success=True,
    ) as visitor:
        exprs = extract_expressions(expr)
        for expr in exprs:
            expr.validate(visitor)
    validation_results = list(visitor.results())

    # Sort both lists by type and status to ensure consistent comparison
    validation_results.sort(key=lambda x: x.type)
    expected.sort(key=lambda x: x["type"])

    assert len(validation_results) == len(expected), (
        f"Expected {len(expected)} validation results, got {len(validation_results)}"
    )

    for actual, ex in zip(validation_results, expected, strict=True):
        assert_validation_detail(actual, **ex)


@pytest.mark.parametrize(
    "expr,expected",
    [
        # Test valid template keys
        (
            {
                "${{ inputs.key_name }}": "value",
            },
            [{"type": ExprType.TEMPLATE_ACTION_INPUT, "status": "success"}],
        ),
        # Test valid step reference in key
        (
            {"${{ steps.step1.result }}": "value"},
            [{"type": ExprType.TEMPLATE_ACTION_STEP, "status": "success"}],
        ),
        # Test invalid input reference
        (
            {"${{ inputs.invalid }}": "value"},
            [
                {
                    "type": ExprType.TEMPLATE_ACTION_INPUT,
                    "status": "error",
                    "contains_msg": "Invalid input reference 'invalid'",
                }
            ],
        ),
        # Test unsupported expression type in key
        (
            {"${{ ACTIONS.some_action.result }}": "value"},
            [
                {
                    "type": ExprType.ACTION,
                    "status": "error",
                    "contains_msg": "ACTIONS expressions are not supported in Template Actions",
                }
            ],
        ),
    ],
)
def test_validate_template_action_key_expressions(expr, expected):
    """Test validation of expressions in template action dictionary keys."""
    validation_context = TemplateActionValidationContext(
        expects={"key_name": ExpectedField(type="str")}, step_refs={"step1"}
    )

    visitor = TemplateActionExprValidator(validation_context=validation_context)
    exprs = extract_expressions(expr)
    for expr in exprs:
        expr.validate(visitor)

    validation_results = list(visitor.results())
    for actual, ex in zip(validation_results, expected, strict=True):
        assert_validation_result(actual, **ex)


def test_multiple_secrets_in_single_template():
    expr = "${{ FN.concat(SECRETS.a.K1, '-', SECRETS.a.K2, '-', SECRETS.b.K3) }}"
    got = sorted(extract_templated_secrets(expr))
    assert got == sorted(["a.K1", "a.K2", "b.K3"])


def test_trims_whitespace_and_newlines_inside_template():
    expr = "${{  FN.to_base64(  SECRETS.a.K1  +\n  '-'  +  SECRETS.b.K2  )  }}"
    got = sorted(extract_templated_secrets(expr))
    assert got == sorted(["a.K1", "b.K2"])


def test_multiple_templates_in_one_string():
    s = "before ${{ SECRETS.a.K1 }} mid ${{ FN.upper(SECRETS.b.K2) }} after"
    got = sorted(extract_templated_secrets(s))
    assert got == sorted(["a.K1", "b.K2"])


def test_detection_in_dict_keys_and_values():
    obj = {
        "${{ FN.lower(SECRETS.a.K1) }}": "${{ SECRETS.b.K2 }}",
        "k2": "${{ FN.len(SECRETS.c.K3) }}",
    }
    got = sorted(extract_templated_secrets(obj))
    assert got == sorted(["a.K1", "b.K2", "c.K3"])


def test_underscore_and_case_in_identifiers():
    expr = "${{ FN.f(SECRETS.alpha_beta.GAMMA_DELTA + SECRETS.xYz.A_b1) }}"
    got = sorted(extract_templated_secrets(expr))
    assert got == sorted(["alpha_beta.GAMMA_DELTA", "xYz.A_b1"])


def test_duplicates_are_deduped():
    expr = "${{ SECRETS.a.K1 + SECRETS.a.K1 + FN.id(SECRETS.a.K1) + SECRETS.b.K2 }}"
    got = sorted(extract_templated_secrets(expr))
    assert got == sorted(["a.K1", "b.K2"])  # no duplicates


def test_ignores_non_template_mentions():
    s = "SECRETS.a.K1 not inside template; and ${{ 'SECRETS.a.K1' }} as string"
    got = extract_templated_secrets(s)
    assert got == []


def test_mixed_nested_object():
    mixed_args_obj = {
        "config": {
            "auth": "${{ SECRETS.alpha.KEY }}",
            "nested": {"value": "${{ FN.concat(SECRETS.beta.SECRET, '-suffix') }}"},
        },
        "items": [
            "${{ SECRETS.gamma.TOKEN }}",
            {"inner": "${{ SECRETS.alpha.KEY }}"},  # duplicate should be deduped
        ],
    }
    got = sorted(extract_templated_secrets(mixed_args_obj))
    assert got == sorted(["alpha.KEY", "beta.SECRET", "gamma.TOKEN"])


def test_collect_expressions_captures_vars_references():
    """Test that collect_expressions correctly captures VARS expressions.

    This verifies that the ExprPathCollector visitor method is named 'vars'
    to match the grammar, not 'variables'. The grammar emits 'vars' nodes,
    so the visitor method must be named accordingly.
    """
    # Test single VARS reference
    obj = {"param": "${{ VARS.my_variable }}"}
    result = collect_expressions(obj)
    assert result.variables == {"my_variable"}

    # Test multiple VARS references
    obj = {
        "api_url": "${{ VARS.api_config.base_url }}",
        "timeout": "${{ VARS.api_config.timeout }}",
        "retry_count": "${{ VARS.retry_settings.max_retries }}",
    }
    result = collect_expressions(obj)
    assert result.variables == {"api_config", "retry_settings"}

    # Test VARS with SECRETS together
    obj = {
        "auth": "${{ SECRETS.my_secret.API_KEY }}",
        "endpoint": "${{ VARS.endpoints.production }}",
    }
    result = collect_expressions(obj)
    assert result.secrets == {"my_secret.API_KEY"}
    assert result.variables == {"endpoints"}

    # Test VARS in complex expressions
    obj = {
        "url": "${{ FN.concat(VARS.base_url, '/api/v1') }}",
        "headers": {
            "Authorization": "${{ SECRETS.auth.TOKEN }}",
            "X-API-Version": "${{ VARS.api_version }}",
        },
    }
    result = collect_expressions(obj)
    assert result.secrets == {"auth.TOKEN"}
    assert result.variables == {"base_url", "api_version"}

    # Test multiple references to same variable (should dedupe)
    obj = {
        "url1": "${{ VARS.config }}",
        "url2": "${{ VARS.config.nested }}",
        "url3": "${{ FN.upper(VARS.config) }}",
    }
    result = collect_expressions(obj)
    assert result.variables == {"config"}

    # Test nested VARS in lists
    obj = {
        "items": [
            "${{ VARS.item1 }}",
            {"nested": "${{ VARS.item2 }}"},
            "${{ FN.format('{} - {}', VARS.item3, VARS.item1) }}",  # item1 duplicate
        ],
    }
    result = collect_expressions(obj)
    assert result.variables == {"item1", "item2", "item3"}

    # Test that non-VARS expressions don't create false positives
    obj = {
        "action": "${{ ACTIONS.my_action.result }}",
        "trigger": "${{ TRIGGER.data }}",
        "env": "${{ ENV.some_var }}",
        "local": "${{ var.x }}",
    }
    result = collect_expressions(obj)
    assert result.variables == set()  # No VARS expressions
    assert result.secrets == set()  # No SECRETS expressions
