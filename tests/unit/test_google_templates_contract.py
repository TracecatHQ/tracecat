"""Credential-chain guards for the Google Workspace templates.

Every Google template resolves credentials through `tools.google_api.call_api`.
Two invariants hold the documented precedence together and are easy to break
silently in a single YAML file:

- every declared secret is optional, or the `||` chain never evaluates;
- every `call_api` step passes `scopes`, which is what places the
  `GOOGLE_API_CREDENTIALS` key ahead of the generic `GOOGLE_SERVICE_TOKEN`;
- `google_admin` is the credential of the Admin SDK namespaces only.
"""

from pathlib import Path

import pytest
from tracecat_registry import RegistryOAuthSecret, RegistrySecret

from tracecat.registry.actions.schemas import TemplateAction

TEMPLATES = (
    Path(__file__).resolve().parents[2]
    / "packages"
    / "tracecat-registry"
    / "tracecat_registry"
    / "templates"
    / "tools"
)
GOOGLE_NAMESPACES = (
    "google_drive",
    "gmail",
    "google_sheets",
    "google_docs",
    "google_slides",
    "google_forms",
    "google_directory",
    "google_reports",
    "google_alert_center",
)
ADMIN_SDK_NAMESPACES = ("google_directory", "google_reports", "google_alert_center")
GOOGLE_TEMPLATES = sorted(
    path for ns in GOOGLE_NAMESPACES for path in (TEMPLATES / ns).rglob("*.yml")
)


@pytest.mark.parametrize(
    "path", GOOGLE_TEMPLATES, ids=lambda p: str(p.relative_to(TEMPLATES))
)
def test_google_template_credential_chain(path: Path) -> None:
    definition = TemplateAction.from_yaml(path).definition

    secrets = definition.secrets or []
    assert secrets, "template declares no secrets"
    assert all(secret.optional for secret in secrets), (
        "every secret in the chain must be optional"
    )
    json_secrets = [
        s for s in secrets if isinstance(s, RegistrySecret) and s.name == "google_api"
    ]
    assert len(json_secrets) == 1, "template must declare the google_api secret once"
    assert json_secrets[0].optional_keys == ["GOOGLE_API_SUBJECT"]

    call_api_steps = [
        step for step in definition.steps if step.action == "tools.google_api.call_api"
    ]
    assert len(call_api_steps) == 1, "template must make exactly one call_api call"
    args = call_api_steps[0].args

    access_token = args.get("access_token")
    assert isinstance(access_token, str) and "SECRETS." in access_token
    assert "_TOKEN" in access_token

    oauth_providers = {
        s.provider_id for s in secrets if isinstance(s, RegistryOAuthSecret)
    }
    namespace = path.relative_to(TEMPLATES).parts[0]
    if namespace in ADMIN_SDK_NAMESPACES:
        assert oauth_providers == {"google_admin"}
        assert (
            access_token
            == "${{ SECRETS.google_admin_oauth.GOOGLE_ADMIN_SERVICE_TOKEN }}"
        )
    else:
        assert "google_admin" not in oauth_providers, (
            "google_admin is an Admin SDK credential only"
        )
        assert "google_admin" not in access_token

    scopes = args.get("scopes")
    assert isinstance(scopes, list) and scopes, "call_api step must pass scopes"
    assert all(isinstance(s, str) and s.startswith("https://") for s in scopes)

    assert "subject" not in args, (
        "subject comes from GOOGLE_API_SUBJECT or the JSON key, not the template"
    )
