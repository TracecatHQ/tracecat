"""Tests for local log hashing commands."""

from __future__ import annotations

import hmac
import json
from hashlib import sha256
from typing import Any

from tracecat_admin.cli import app
from tracecat_admin.logs import (
    LOG_HASH_VERSION,
    LogIdentifierType,
    compute_log_search_hash,
    normalize_identifier_value,
)
from typer.testing import CliRunner

runner = CliRunner()


def _expected_hash(identifier_type: str, value: str, key: str) -> str:
    payload = f"{identifier_type}:{value}".encode()
    digest = hmac.new(key.encode("utf-8"), payload, sha256).hexdigest()
    return f"{LOG_HASH_VERSION}_{digest}"


class TestNormalizeIdentifierValue:
    """Tests for identifier normalization."""

    def test_normalizes_email_with_trim_and_casefold(self) -> None:
        normalized = normalize_identifier_value(
            LogIdentifierType.EMAIL, "  Alice.Example+tag@Example.COM  "
        )

        assert normalized == "alice.example+tag@example.com"

    def test_normalizes_username_with_trim_only(self) -> None:
        normalized = normalize_identifier_value(
            LogIdentifierType.USERNAME, "  MixedCaseUser  "
        )

        assert normalized == "MixedCaseUser"

    def test_normalizes_external_account_id_with_trim_only(self) -> None:
        normalized = normalize_identifier_value(
            LogIdentifierType.EXTERNAL_ACCOUNT_ID, "  Ext-User-123  "
        )

        assert normalized == "Ext-User-123"


class TestComputeLogSearchHash:
    """Tests for log hash computation."""

    def test_returns_versioned_field_specific_hash(self) -> None:
        result = compute_log_search_hash(
            LogIdentifierType.EMAIL,
            "  Alice@Example.com ",
            key="test-hmac-key",
        )

        assert result.field_name == "email_hash"
        assert result.hash_value == _expected_hash(
            "email", "alice@example.com", "test-hmac-key"
        )


class TestLogsHashCommand:
    """Tests for the local logs hash command."""

    def test_hash_command_uses_direct_env_key(self, monkeypatch: Any) -> None:
        monkeypatch.setenv("TRACECAT__LOG_REDACTION_HMAC_KEY", "cli-hmac-key")

        result = runner.invoke(
            app,
            ["logs", "hash", "--type", "email", "  Alice@Example.com "],
        )

        assert result.exit_code == 0
        assert result.stdout.strip() == "email_hash=" + _expected_hash(
            "email", "alice@example.com", "cli-hmac-key"
        )

    def test_hash_command_supports_json_output(self, monkeypatch: Any) -> None:
        monkeypatch.setenv("TRACECAT__LOG_REDACTION_HMAC_KEY", "cli-hmac-key")

        result = runner.invoke(
            app,
            ["logs", "hash", "--type", "username", "--json", " MixedCaseUser "],
        )

        assert result.exit_code == 0
        payload = json.loads(result.stdout)
        assert payload == {
            "identifier_type": "username",
            "field_name": "username_hash",
            "hash_value": _expected_hash("username", "MixedCaseUser", "cli-hmac-key"),
        }

    def test_hash_command_uses_aws_secret_manager_arn(self, monkeypatch: Any) -> None:
        class FakeSecretsManagerClient:
            def get_secret_value(self, SecretId: str) -> dict[str, str]:
                assert SecretId == "arn:aws:secretsmanager:us-east-1:123:secret:logs"
                return {"SecretString": '{"key": "aws-hmac-key"}'}

        class FakeSession:
            def client(self, *, service_name: str) -> FakeSecretsManagerClient:
                assert service_name == "secretsmanager"
                return FakeSecretsManagerClient()

        monkeypatch.setenv(
            "TRACECAT__LOG_REDACTION_HMAC_KEY__ARN",
            "arn:aws:secretsmanager:us-east-1:123:secret:logs",
        )
        monkeypatch.setattr(
            "tracecat_admin.config.boto3.session.Session",
            FakeSession,
        )

        result = runner.invoke(
            app,
            [
                "logs",
                "hash",
                "--type",
                "external_account_id",
                "  Ext-User-123  ",
            ],
        )

        assert result.exit_code == 0
        assert result.stdout.strip() == "external_account_id_hash=" + _expected_hash(
            "external_account_id", "Ext-User-123", "aws-hmac-key"
        )

    def test_hash_command_requires_hmac_key(self) -> None:
        result = runner.invoke(app, ["logs", "hash", "--type", "email", "alice"])

        assert result.exit_code == 1
        assert "TRACECAT__LOG_REDACTION_HMAC_KEY" in result.stderr
