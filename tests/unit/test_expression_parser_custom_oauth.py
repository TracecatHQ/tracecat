from tracecat.expressions.parser.core import parser
from tracecat.expressions.parser.evaluator import ExprEvaluator


def test_parse_custom_oauth_provider_with_underscore():
    """Test that custom OAuth provider IDs with underscores parse correctly."""
    raw_expression = "SECRETS.custom_secureapp_oauth.CUSTOM_SECUREAPP_SERVICE_TOKEN"

    result = parser.parse(raw_expression)
    assert result is not None

    # Test evaluation
    operand = {
        "SECRETS": {
            "custom_secureapp_oauth": {
                "CUSTOM_SECUREAPP_SERVICE_TOKEN": "test-token-123"
            }
        }
    }

    evaluator = ExprEvaluator(operand=operand)
    value = evaluator.evaluate(result)
    assert value == "test-token-123"


def test_parse_custom_oauth_provider_names_with_underscores():
    """Test various custom provider name patterns."""

    test_cases = [
        "SECRETS.custom_my_app.TOKEN",
        "SECRETS.custom_secure_app_v2.API_KEY",
        "SECRETS.custom_company_integration_1.SECRET",
    ]

    for expression in test_cases:
        result = parser.parse(expression)
        assert result is not None, f"Failed to parse: {expression}"
