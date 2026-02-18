import pytest
from litellm.proxy._types import ProxyException

from tracecat.agent.gateway import _inject_provider_credentials


def test_gemini_injects_api_key_and_prefixes_model():
    data = {"model": "gemini-2.5-flash"}
    creds = {"GEMINI_API_KEY": "test-gemini-key"}

    _inject_provider_credentials(data, "gemini", creds)

    assert data["api_key"] == "test-gemini-key"
    assert data["model"] == "gemini/gemini-2.5-flash"


def test_gemini_does_not_double_prefix_model():
    data = {"model": "gemini/gemini-3-flash-preview"}
    creds = {"GEMINI_API_KEY": "test-gemini-key"}

    _inject_provider_credentials(data, "gemini", creds)

    assert data["model"] == "gemini/gemini-3-flash-preview"


def test_vertex_ai_injects_project_credentials_and_model():
    data = {"model": "vertex_ai"}
    creds = {
        "GOOGLE_API_CREDENTIALS": '{"type":"service_account"}',
        "GOOGLE_CLOUD_PROJECT": "my-gcp-project",
        "VERTEX_AI_MODEL": "gemini-2.5-flash",
        "GOOGLE_CLOUD_LOCATION": "us-central1",
    }

    _inject_provider_credentials(data, "vertex_ai", creds)

    assert data["vertex_credentials"] == '{"type":"service_account"}'
    assert data["vertex_project"] == "my-gcp-project"
    assert data["vertex_location"] == "us-central1"
    assert data["model"] == "vertex_ai/gemini-2.5-flash"


def test_vertex_ai_requires_credentials_project_and_model():
    data = {"model": "vertex_ai"}
    creds = {
        "GOOGLE_API_CREDENTIALS": '{"type":"service_account"}',
    }

    with pytest.raises(ProxyException):
        _inject_provider_credentials(data, "vertex_ai", creds)


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


def test_bedrock_rejects_session_token_without_static_keys():
    data = {"model": "bedrock"}
    creds = {
        "AWS_SESSION_TOKEN": "session-token",
        "AWS_INFERENCE_PROFILE_ID": "us.anthropic.claude-sonnet-4-20250514-v1:0",
    }

    with pytest.raises(ProxyException):
        _inject_provider_credentials(data, "bedrock", creds)
