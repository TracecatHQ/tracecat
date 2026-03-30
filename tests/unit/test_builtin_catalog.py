from tracecat.agent.builtin_catalog import get_builtin_catalog_models
from tracecat.agent.types import ModelSourceType


def test_builtin_catalog_maps_bedrock_entries_into_bedrock_provider() -> None:
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
    assert opus_46.metadata["upstream_provider"] == "bedrock"

    assert sonnet_46.source_type is ModelSourceType.BEDROCK
    assert sonnet_46.model_provider == ModelSourceType.BEDROCK.value
    assert sonnet_46.display_name == "anthropic.claude-sonnet-4-6"
    assert sonnet_46.metadata["upstream_provider"] == "bedrock"


def test_builtin_catalog_filters_legacy_claude_gpt_and_non_agent_families() -> None:
    rows = get_builtin_catalog_models()
    model_ids = {row.model_id for row in rows}
    identities = {(row.model_provider, row.model_id) for row in rows}

    assert "claude-3-5-sonnet-20241022" not in model_ids
    assert "claude-3-7-sonnet-20250219" not in model_ids
    assert "anthropic.claude-3-5-sonnet-20241022-v2:0" not in model_ids
    assert "gpt-4o-mini" not in model_ids
    assert "chatgpt-4o-latest" not in model_ids
    assert ("openai", "gpt-5-chat-latest") not in identities
    assert ("openai", "gpt-5.1-chat-latest") not in identities
    assert "gpt-5.4-pro" not in model_ids
    assert "gpt-5-codex" not in model_ids
    assert "o1" not in model_ids
    assert "o3" not in model_ids
    assert "o4-mini" not in model_ids
    assert "gpt-audio" not in model_ids
    assert "gpt-realtime-2025-08-28" not in model_ids
    assert "gpt-4o-mini-tts-2025-03-20" not in model_ids
    assert "gpt-image-1" not in model_ids
    assert "gemini-2.5-flash-image" not in model_ids
    assert "gemini-live-2.5-flash-preview-native-audio-09-2025" not in model_ids
    assert "gemini-2.5-computer-use-preview-10-2025" not in model_ids
    assert "gemini-embedding-001" not in model_ids
    assert "gemini-exp-1206" not in model_ids
    assert "gemini-3-pro-preview" not in model_ids
    assert "gemma-3-27b-it" not in model_ids
    assert "imagen-4.0-generate-001" not in model_ids


def test_builtin_catalog_keeps_current_claude_and_gpt5_families() -> None:
    rows = get_builtin_catalog_models()
    identities = {(row.model_provider, row.model_id) for row in rows}

    assert ("bedrock", "anthropic.claude-sonnet-4-6") in identities
    assert ("bedrock", "anthropic.claude-opus-4-6-v1") in identities
    assert ("bedrock", "amazon.nova-2-pro-preview-20251202-v1:0") in identities
    assert ("openai", "gpt-5") in identities
    assert ("openai", "gpt-5.4") in identities
    assert ("openai", "gpt-5.4-mini") in identities
    assert ("azure_openai", "gpt-5") in identities
    assert ("azure_openai", "gpt-5.4") in identities
    assert ("anthropic", "claude-sonnet-4-6") in identities
    assert ("gemini", "gemini-2.5-flash") in identities
    assert ("gemini", "gemini-2.5-pro") in identities
    assert ("gemini", "gemini-3.1-pro-preview") in identities
    assert ("vertex_ai", "gemini-3.1-pro-preview") in identities
    assert ("azure_ai", "grok-4") in identities
