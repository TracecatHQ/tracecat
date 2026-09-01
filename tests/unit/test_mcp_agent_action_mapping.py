"""Drift tests for ``MCP_AGENT_ACTION_PROVIDER_IDS``.

Each legacy MCP action declares its OAuth provider id in three places: the DSL
mapping, the registry action's secret, and the OAuth provider class. These
tests fail when any one of them drifts from the others.
"""

from __future__ import annotations

import pytest
from tracecat_registry import RegistryOAuthSecret

from tracecat.dsl.enums import MCP_AGENT_ACTION_PROVIDER_IDS
from tracecat.integrations.enums import OAuthGrantType
from tracecat.integrations.providers import get_provider_class
from tracecat.integrations.schemas import ProviderKey
from tracecat.registry.repository import Repository


@pytest.fixture(scope="module")
def registry_repo() -> Repository:
    repo = Repository()
    repo.init()
    return repo


def _oauth_secrets(repo: Repository, action: str) -> list[RegistryOAuthSecret]:
    bound = repo.get(action)
    assert bound is not None, f"{action} not registered"
    return [s for s in (bound.secrets or []) if isinstance(s, RegistryOAuthSecret)]


@pytest.mark.parametrize(
    ("action", "provider_id"), sorted(MCP_AGENT_ACTION_PROVIDER_IDS.items())
)
def test_mapped_action_declares_matching_oauth_secret(
    registry_repo: Repository, action: str, provider_id: str
) -> None:
    secrets = _oauth_secrets(registry_repo, action)
    assert [s.provider_id for s in secrets] == [provider_id]


@pytest.mark.parametrize(
    ("action", "provider_id"), sorted(MCP_AGENT_ACTION_PROVIDER_IDS.items())
)
def test_mapped_provider_id_resolves_to_provider_class(
    registry_repo: Repository, action: str, provider_id: str
) -> None:
    # Grant type comes from the action's own secret so the test tracks it.
    (secret,) = _oauth_secrets(registry_repo, action)
    grant_type = OAuthGrantType(secret.grant_type)
    key = ProviderKey(id=provider_id, grant_type=grant_type)
    assert get_provider_class(key) is not None, f"no provider class for {key}"


def test_every_mcp_tool_action_is_mapped(registry_repo: Repository) -> None:
    """A ``tools.*.mcp`` action with an ``*_mcp`` OAuth secret must be mapped.

    Providers without a legacy action (e.g. ``secureannex_mcp``) are not
    covered here, since this only walks registered actions.
    """
    discovered: dict[str, str] = {}
    for key in registry_repo.keys:
        parts = key.split(".")
        if len(parts) != 3 or parts[0] != "tools" or parts[2] != "mcp":
            continue
        for secret in _oauth_secrets(registry_repo, key):
            if secret.provider_id.endswith("_mcp"):
                discovered[key] = secret.provider_id

    assert discovered == dict(MCP_AGENT_ACTION_PROVIDER_IDS)
