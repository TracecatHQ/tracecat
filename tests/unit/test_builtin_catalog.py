from tracecat.agent.builtin_catalog import get_builtin_catalog_models
from tracecat.agent.types import ModelSourceType


def test_builtin_catalog_maps_bedrock_converse_into_bedrock_provider() -> None:
    rows = get_builtin_catalog_models()

    opus_46 = next(
        row for row in rows if row.model_id == "anthropic.claude-opus-4-6-v1"
    )
    sonnet_46 = next(
        row for row in rows if row.model_id == "anthropic.claude-sonnet-4-6"
    )

    assert opus_46.source_type is ModelSourceType.BEDROCK
    assert opus_46.model_provider == ModelSourceType.BEDROCK.value
    assert opus_46.display_name == "anthropic.claude-opus-4-6-v1"
    assert opus_46.metadata["upstream_provider"] == "bedrock_converse"

    assert sonnet_46.source_type is ModelSourceType.BEDROCK
    assert sonnet_46.model_provider == ModelSourceType.BEDROCK.value
    assert sonnet_46.display_name == "anthropic.claude-sonnet-4-6"
    assert sonnet_46.metadata["upstream_provider"] == "bedrock_converse"


def test_builtin_catalog_filters_legacy_claude_gpt_and_reasoning_families() -> None:
    rows = get_builtin_catalog_models()
    model_ids = {row.model_id for row in rows}

    assert "claude-3-5-sonnet-20241022" not in model_ids
    assert "anthropic.claude-3-5-sonnet-20241022-v2:0" not in model_ids
    assert "bedrock/us.anthropic.claude-3-5-haiku-20241022-v1:0" not in model_ids
    assert "gpt-4o-mini" not in model_ids
    assert "azure/gpt-4o-mini" not in model_ids
    assert "chatgpt-4o-latest" not in model_ids
    assert "azure/o1" not in model_ids
    assert "azure/o3" not in model_ids
    assert "azure/o4-mini" not in model_ids
    assert "gpt-audio" not in model_ids
    assert "azure/gpt-realtime-2025-08-28" not in model_ids
    assert "gpt-4o-mini-tts-2025-03-20" not in model_ids
    assert "gpt-image-1" not in model_ids
    assert "gemini-2.5-flash-image" not in model_ids
    assert "gemini-live-2.5-flash-preview-native-audio-09-2025" not in model_ids
    assert "gemini-2.5-computer-use-preview-10-2025" not in model_ids
    assert "gemini-embedding-001" not in model_ids
    assert "gemini-exp-1206" not in model_ids
    assert "gemini/gemma-3-27b-it" not in model_ids
    assert "gemini/imagen-4.0-generate-001" not in model_ids


def test_builtin_catalog_keeps_current_claude_and_gpt5_families() -> None:
    rows = get_builtin_catalog_models()
    model_ids = {row.model_id for row in rows}

    assert "anthropic.claude-sonnet-4-6" in model_ids
    assert "anthropic.claude-opus-4-6-v1" in model_ids
    assert "gpt-5" in model_ids
    assert "gpt-5-mini" in model_ids
    assert "gemini/gemini-2.5-flash" in model_ids
    assert "gemini/gemini-2.5-pro" in model_ids
