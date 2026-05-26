from __future__ import annotations

from typing import Any, cast

import pytest
from pydantic import SecretStr
from tracecat_registry import RegistrySecret

from tracecat.secrets.constants import DEFAULT_SECRETS_ENVIRONMENT
from tracecat.secrets.schemas import SecretKeyValue, SecretSearch
from tracecat.validation.service import validate_single_secret


class FakeSecretsService:
    def __init__(self, *, search_results: list[Any]):
        self.search_results = search_results
        self.search_params: SecretSearch | None = None

    async def get_secret_by_name(self, *args: Any, **kwargs: Any) -> None:
        raise AssertionError("validation should use search_secrets")

    async def search_secrets(self, params: SecretSearch) -> list[Any]:
        self.search_params = params
        return self.search_results

    def decrypt_keys(self, encrypted_keys: bytes) -> list[SecretKeyValue]:
        assert encrypted_keys == b"encrypted"
        return [
            SecretKeyValue(
                key="VIRUSTOTAL_API_KEY",
                value=SecretStr("test-value"),
            )
        ]


@pytest.mark.anyio
async def test_validate_single_secret_accepts_searched_secret() -> None:
    service = FakeSecretsService(
        search_results=[type("SecretView", (), {"encrypted_keys": b"encrypted"})()]
    )
    checked: set[str] = set()

    results = await validate_single_secret(
        cast(Any, service),
        checked,
        DEFAULT_SECRETS_ENVIRONMENT,
        RegistrySecret(name="virustotal", keys=["VIRUSTOTAL_API_KEY"]),
    )

    assert results == []
    assert checked == {"virustotal"}
    assert service.search_params == SecretSearch(
        names={"virustotal"}, environment=DEFAULT_SECRETS_ENVIRONMENT
    )


@pytest.mark.anyio
async def test_validate_single_secret_reports_missing_searched_secret() -> None:
    service = FakeSecretsService(search_results=[])
    checked: set[str] = set()

    results = await validate_single_secret(
        cast(Any, service),
        checked,
        DEFAULT_SECRETS_ENVIRONMENT,
        RegistrySecret(name="virustotal", keys=["VIRUSTOTAL_API_KEY"]),
    )

    assert len(results) == 1
    assert "missing in the secrets manager" in results[0].msg
    assert results[0].detail is not None
    assert results[0].detail["secret_name"] == "virustotal"
    assert checked == {"virustotal"}


@pytest.mark.anyio
async def test_validate_single_secret_reports_duplicate_searched_secret() -> None:
    duplicate = type("SecretView", (), {"encrypted_keys": b"encrypted"})()
    service = FakeSecretsService(search_results=[duplicate, duplicate])

    results = await validate_single_secret(
        cast(Any, service),
        set(),
        DEFAULT_SECRETS_ENVIRONMENT,
        RegistrySecret(name="virustotal", keys=["VIRUSTOTAL_API_KEY"]),
    )

    assert len(results) == 1
    assert "Multiple secrets found" in results[0].msg
