import pytest
from litellm.proxy.proxy_server import ProxyException

from tracecat.agent.gateway import _inject_provider_credentials


def test_bedrock_falls_back_to_ambient_iam_role_when_static_keys_missing():
    data = {"model": "bedrock"}
    creds = {
        "AWS_REGION": "us-east-1",
        "AWS_INFERENCE_PROFILE_ID": "us.anthropic.claude-sonnet-4-20250514-v1:0",
    }

    _inject_provider_credentials(data, "bedrock", creds)

    assert "api_key" not in data
    assert "aws_access_key_id" not in data
    assert "aws_secret_access_key" not in data
    assert data["aws_region_name"] == "us-east-1"
    assert data["model"] == "bedrock/us.anthropic.claude-sonnet-4-20250514-v1:0"


def test_bedrock_uses_static_keys_when_configured():
    data = {"model": "bedrock"}
    creds = {
        "AWS_ACCESS_KEY_ID": "AKIA123",
        "AWS_SECRET_ACCESS_KEY": "secret",
        "AWS_SESSION_TOKEN": "session-token",
        "AWS_REGION": "us-west-2",
        "AWS_MODEL_ID": "anthropic.claude-3-haiku-20240307-v1:0",
    }

    _inject_provider_credentials(data, "bedrock", creds)

    assert data["aws_access_key_id"] == "AKIA123"
    assert data["aws_secret_access_key"] == "secret"
    assert data["aws_session_token"] == "session-token"
    assert data["aws_region_name"] == "us-west-2"
    assert data["model"] == "bedrock/anthropic.claude-3-haiku-20240307-v1:0"


def test_bedrock_rejects_partial_static_keys():
    data = {"model": "bedrock"}
    creds = {
        "AWS_ACCESS_KEY_ID": "AKIA123",
        "AWS_INFERENCE_PROFILE_ID": "us.anthropic.claude-sonnet-4-20250514-v1:0",
    }

    with pytest.raises(ProxyException):
        _inject_provider_credentials(data, "bedrock", creds)
