from __future__ import annotations

from types import SimpleNamespace
from typing import cast
from uuid import uuid4

from tracecat.db.models import ServiceAccount, ServiceAccountApiKey
from tracecat.service_accounts.service import IssuedServiceAccountApiKeyResult


def test_issued_service_account_api_key_result_exposes_api_key_id() -> None:
    service_account = cast(ServiceAccount, SimpleNamespace(id=uuid4()))
    api_key_id = uuid4()
    api_key = cast(ServiceAccountApiKey, SimpleNamespace(id=api_key_id))

    result = IssuedServiceAccountApiKeyResult(
        service_account=service_account,
        api_key=api_key,
        raw_key="tc_ws_sk_raw",
    )

    unpacked_service_account, unpacked_api_key, raw_key = result

    assert unpacked_service_account is service_account
    assert unpacked_api_key is api_key
    assert raw_key == "tc_ws_sk_raw"
    assert result.api_key_id == api_key_id
