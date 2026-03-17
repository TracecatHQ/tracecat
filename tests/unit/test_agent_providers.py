from pydantic_ai.models.openai import OpenAIChatModel

from tracecat.agent.providers import get_model


def test_get_model_supports_direct_endpoint_provider() -> None:
    model = get_model(
        model_name="qwen2.5:7b",
        model_provider="direct_endpoint",
        base_url="http://localhost:11434/v1",
    )

    assert isinstance(model, OpenAIChatModel)
