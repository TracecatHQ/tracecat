from tracecat.agent.builtin_catalog import get_builtin_catalog_models
from tracecat.agent.types import ModelSourceType


def test_builtin_catalog_maps_bedrock_converse_into_bedrock_provider() -> None:
    rows = get_builtin_catalog_models()

    opus_46 = next(
        row for row in rows if row.raw_model_id == "anthropic.claude-opus-4-6-v1"
    )
    sonnet_46 = next(
        row for row in rows if row.raw_model_id == "anthropic.claude-sonnet-4-6"
    )

    assert opus_46.source_type is ModelSourceType.BEDROCK
    assert opus_46.runtime_provider == ModelSourceType.BEDROCK.value
    assert opus_46.metadata["upstream_provider"] == "bedrock_converse"

    assert sonnet_46.source_type is ModelSourceType.BEDROCK
    assert sonnet_46.runtime_provider == ModelSourceType.BEDROCK.value
    assert sonnet_46.metadata["upstream_provider"] == "bedrock_converse"
