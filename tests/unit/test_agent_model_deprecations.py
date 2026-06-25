from tracecat.agent.model_deprecations import (
    deprecation_message,
    is_deprecated_model,
    is_hidden_model,
)
from tracecat.chat.schemas import BasicChatRequest, VercelChatRequest


def test_gpt_4o_mini_is_hidden_only_for_platform_openai() -> None:
    assert is_deprecated_model("openai", "gpt-4o-mini") is True
    assert is_hidden_model("openai", "gpt-4o-mini") is True
    assert deprecation_message("openai", "gpt-4o-mini") is not None

    assert is_deprecated_model("azure-openai", "gpt-4o-mini") is False
    assert is_hidden_model("azure-openai", "gpt-4o-mini") is False
    assert deprecation_message("azure-openai", "gpt-4o-mini") is None


def test_chat_request_defaults_use_supported_model() -> None:
    assert BasicChatRequest(message="hello").model_name == "gpt-5-mini"
    assert VercelChatRequest.model_fields["model"].default == "gpt-5-mini"
