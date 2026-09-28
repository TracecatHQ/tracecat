"""Schema-level validation tests for MCP integration URL credentials.

URL validation is driven solely by the catalog declaring ``type: "url"`` on a
credential — there is no env-var-name heuristic.
"""

from __future__ import annotations

import pytest

from tracecat.integrations.catalog.loader import (
    get_platform_mcp_catalog_entry_by_slug,
)
from tracecat.integrations.schemas import (
    MCPStdioIntegrationCreate,
    validate_url_credential_values,
)
from tracecat.integrations.service import IntegrationService


class TestValidateUrlCredentialValues:
    """``validate_url_credential_values`` checks only the declared ``url_keys``."""

    def test_scheme_less_url_rejected(self) -> None:
        with pytest.raises(ValueError, match="http://"):
            validate_url_credential_values(
                {"PURPLEMCP_CONSOLE_BASE_URL": "usea1-015.sentinelone.net"},
                {"PURPLEMCP_CONSOLE_BASE_URL"},
            )

    def test_valid_https_url_accepted(self) -> None:
        # Does not raise.
        validate_url_credential_values(
            {"PURPLEMCP_CONSOLE_BASE_URL": "https://usea1-015.sentinelone.net"},
            {"PURPLEMCP_CONSOLE_BASE_URL"},
        )

    def test_non_http_scheme_rejected(self) -> None:
        with pytest.raises(ValueError, match="http://"):
            validate_url_credential_values({"X": "ftp://host/x"}, {"X"})

    def test_template_expression_skipped(self) -> None:
        """Templated values resolve at run time, so format is not checked."""
        validate_url_credential_values(
            {"CONSOLE_BASE_URL": "${{ VARS.console_url }}"},
            {"CONSOLE_BASE_URL"},
        )

    def test_empty_value_skipped(self) -> None:
        """An unfilled placeholder must not fail validation."""
        validate_url_credential_values({"CONSOLE_BASE_URL": ""}, {"CONSOLE_BASE_URL"})

    def test_key_not_declared_url_is_ignored(self) -> None:
        """A key not in ``url_keys`` is never validated as a URL.

        This is the whole point of dropping the name heuristic: a ``*_URL`` key
        the catalog did not mark ``type: "url"`` passes through untouched.
        """
        validate_url_credential_values(
            {"SOME_OTHER_URL": "no-scheme.example.com"},
            set(),
        )

    def test_missing_key_is_noop(self) -> None:
        """A declared url key absent from the values is a no-op."""
        validate_url_credential_values({"OTHER": "value"}, {"CONSOLE_BASE_URL"})

    @pytest.mark.parametrize(
        "value",
        [
            "${{",
            "${{ VARS.console_url",
            "prefix${{suffix",
        ],
    )
    def test_malformed_template_not_skipped(self, value: str) -> None:
        """A bare ``${{`` with no closing ``}}`` is not a template, so the value
        is still subject to URL validation rather than silently bypassing it."""
        with pytest.raises(ValueError, match="http://"):
            validate_url_credential_values(
                {"CONSOLE_BASE_URL": value}, {"CONSOLE_BASE_URL"}
            )

    def test_template_with_surrounding_text_skipped(self) -> None:
        """A complete template expression embedded in text still resolves at
        run time, so it is skipped."""
        validate_url_credential_values(
            {"CONSOLE_BASE_URL": "https://${{ VARS.host }}/path"},
            {"CONSOLE_BASE_URL"},
        )

    @pytest.mark.parametrize(
        "value",
        [123, ["https://host"], {"url": "https://host"}, None, True],
    )
    def test_non_string_value_rejected(self, value: object) -> None:
        """A present, declared url key with a non-string value must be rejected
        rather than silently skipped — otherwise invalid payloads bypass the
        server-side URL check."""
        with pytest.raises(ValueError, match="http://"):
            validate_url_credential_values(
                {"CONSOLE_BASE_URL": value},  # type: ignore[dict-item]
                {"CONSOLE_BASE_URL"},
            )


class TestCatalogUrlCredentialValidation:
    """Service collects ``type: "url"`` keys from the catalog spec to validate."""

    @staticmethod
    def _sentinelone_spec():
        entry = get_platform_mcp_catalog_entry_by_slug(
            "sentinelone-mcp", include_private=True
        )
        assert entry is not None and entry.connection_spec is not None
        return entry.connection_spec

    def test_scheme_less_url_rejected_via_catalog_type(self) -> None:
        spec = self._sentinelone_spec()
        params = MCPStdioIntegrationCreate(
            name="SentinelOne Purple",
            stdio_command="uvx",
            stdio_env={"PURPLEMCP_CONSOLE_BASE_URL": "usea1-015.sentinelone.net"},
            catalog_slug="sentinelone-mcp",
        )
        with pytest.raises(ValueError, match="http://"):
            IntegrationService._validate_catalog_url_credentials(
                params=params, spec=spec
            )

    def test_valid_url_accepted_via_catalog_type(self) -> None:
        spec = self._sentinelone_spec()
        params = MCPStdioIntegrationCreate(
            name="SentinelOne Purple",
            stdio_command="uvx",
            stdio_env={
                "PURPLEMCP_CONSOLE_BASE_URL": "https://usea1-015.sentinelone.net"
            },
            catalog_slug="sentinelone-mcp",
        )
        # Does not raise.
        IntegrationService._validate_catalog_url_credentials(params=params, spec=spec)

    def test_token_value_not_validated_as_url(self) -> None:
        """The token credential is ``type: "string"`` and is left untouched."""
        spec = self._sentinelone_spec()
        params = MCPStdioIntegrationCreate(
            name="SentinelOne Purple",
            stdio_command="uvx",
            stdio_env={"PURPLEMCP_CONSOLE_TOKEN": "not-a-url"},
            catalog_slug="sentinelone-mcp",
        )
        # Does not raise.
        IntegrationService._validate_catalog_url_credentials(params=params, spec=spec)
