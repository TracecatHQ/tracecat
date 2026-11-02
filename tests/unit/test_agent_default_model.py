"""Legacy default names are resolved without guessing across providers."""

import uuid

from tracecat.agent.catalog.schemas import AgentCatalogRead
from tracecat.agent.default_model import resolve_legacy_default_model


def entry(*, custom: bool) -> AgentCatalogRead:
    return AgentCatalogRead(
        id=uuid.uuid4(),
        custom_provider_id=uuid.uuid4() if custom else None,
        organization_id=uuid.uuid4(),
        model_provider="synthetic-provider",
        model_name="shared-model",
        model_metadata=None,
    )


def test_legacy_default_prefers_unique_builtin_and_rejects_ambiguity():
    builtin = entry(custom=False)
    custom = entry(custom=True)
    assert (
        resolve_legacy_default_model([custom, builtin], model_name="shared-model")
        == builtin
    )
    assert resolve_legacy_default_model([custom], model_name="shared-model") == custom
    assert (
        resolve_legacy_default_model(
            [builtin, entry(custom=False)], model_name="shared-model"
        )
        is None
    )
    assert (
        resolve_legacy_default_model(
            [custom, entry(custom=True)], model_name="shared-model"
        )
        is None
    )
    assert resolve_legacy_default_model([builtin], model_name="missing") is None
