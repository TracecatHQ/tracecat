import json
import subprocess
import sys
from types import SimpleNamespace
from typing import Any

import pytest
from tracecat_registry.core import duckdb as duckdb_action
from tracecat_registry.core.duckdb import (
    _build_s3_secret,
    _extension_directory,
    _thread_limit,
    execute_sql,
)


def test_execute_sql_returns_json_serializable_rows() -> None:
    result = execute_sql("SELECT 1 AS id, 'alpha' AS name")

    assert result == [{"id": 1, "name": "alpha"}]
    json.dumps(result)


def test_execute_sql_non_query_returns_json_serializable() -> None:
    result = execute_sql("SET TimeZone='UTC'")

    assert isinstance(result, (int, list))
    json.dumps(result)


def test_extension_directory_prefers_env(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(
        duckdb_action, "TRACECAT__DUCKDB_EXTENSION_DIRECTORY", "/custom/ext"
    )
    assert _extension_directory() == "/custom/ext"


def test_extension_directory_falls_back_to_default_when_present(
    monkeypatch: pytest.MonkeyPatch, tmp_path
) -> None:
    monkeypatch.setattr(duckdb_action, "TRACECAT__DUCKDB_EXTENSION_DIRECTORY", None)
    monkeypatch.setattr(duckdb_action, "_DEFAULT_EXTENSION_DIRECTORY", str(tmp_path))
    assert _extension_directory() == str(tmp_path)


def test_extension_directory_none_when_unset_and_missing(
    monkeypatch: pytest.MonkeyPatch, tmp_path
) -> None:
    monkeypatch.setattr(duckdb_action, "TRACECAT__DUCKDB_EXTENSION_DIRECTORY", None)
    monkeypatch.setattr(
        duckdb_action, "_DEFAULT_EXTENSION_DIRECTORY", str(tmp_path / "missing")
    )
    assert _extension_directory() is None


class _FakeCredentials:
    def get_frozen_credentials(self) -> SimpleNamespace:
        return SimpleNamespace(access_key="AKIA", secret_key="secret", token="token")


def _patch_s3_session(
    monkeypatch: pytest.MonkeyPatch, secret_region: str | None
) -> dict[str, Any]:
    """Stub the amazon_s3 secret and boto3 session; return the captured kwargs."""
    captured: dict[str, Any] = {}

    def fake_get_sync_session(**kwargs: Any) -> SimpleNamespace:
        captured.update(kwargs)
        return SimpleNamespace(
            region_name=kwargs.get("region_name") or secret_region,
            get_credentials=lambda: _FakeCredentials(),
        )

    monkeypatch.setattr(
        duckdb_action.secrets,
        "get_or_default",
        lambda key, default=None: (
            "arn:aws:iam::123456789012:role/reader"
            if key == "AWS_ROLE_ARN"
            else default
        ),
    )
    monkeypatch.setattr(
        duckdb_action.aws_boto3, "get_sync_session", fake_get_sync_session
    )
    return captured


def test_build_s3_secret_region_name_overrides_secret_region(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    captured = _patch_s3_session(monkeypatch, secret_region="us-east-1")

    spec = _build_s3_secret(region_name="us-west-2")

    assert spec is not None
    options, params = spec
    assert captured["region_name"] == "us-west-2"
    assert options[-1] == "REGION ?"
    assert params[-1] == "us-west-2"


def test_build_s3_secret_omits_region_when_unset(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _patch_s3_session(monkeypatch, secret_region=None)

    spec = _build_s3_secret()

    assert spec is not None
    options, _ = spec
    assert "REGION ?" not in options


def test_thread_limit_none_without_address_space_cap(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(
        duckdb_action.resource,
        "getrlimit",
        lambda _: (duckdb_action.resource.RLIM_INFINITY,) * 2,
    )
    assert _thread_limit() is None


def test_connect_caps_malloc_arenas_before_connect(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    order: list[str] = []
    connection = object()

    def mallopt(option: int, value: int) -> None:
        assert (option, value) == (-8, 2)
        order.append("mallopt")

    def connect(**kwargs: Any) -> object:
        order.append("connect")
        return connection

    limit = 1024 * 1024 * 1024
    monkeypatch.setattr(duckdb_action.sys, "platform", "linux")
    monkeypatch.setattr(duckdb_action.resource, "getrlimit", lambda _: (limit, limit))
    monkeypatch.setattr(
        duckdb_action.ctypes,
        "CDLL",
        lambda _: SimpleNamespace(mallopt=mallopt),
    )
    monkeypatch.setattr(duckdb_action.duckdb, "connect", connect)

    assert duckdb_action._connect() is connection
    assert order == ["mallopt", "connect"]


def test_cap_malloc_arenas_skips_infinite_address_space(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(duckdb_action.sys, "platform", "linux")
    monkeypatch.setattr(
        duckdb_action.resource,
        "getrlimit",
        lambda _: (duckdb_action.resource.RLIM_INFINITY,) * 2,
    )

    def unexpected_cdll(_: None) -> None:
        pytest.fail("CDLL should not be called without an address-space limit")

    monkeypatch.setattr(duckdb_action.ctypes, "CDLL", unexpected_cdll)

    duckdb_action._cap_malloc_arenas()


def test_cap_malloc_arenas_ignores_missing_mallopt(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    limit = 1024 * 1024 * 1024
    monkeypatch.setattr(duckdb_action.sys, "platform", "linux")
    monkeypatch.setattr(duckdb_action.resource, "getrlimit", lambda _: (limit, limit))
    monkeypatch.setattr(duckdb_action.ctypes, "CDLL", lambda _: SimpleNamespace())

    duckdb_action._cap_malloc_arenas()


@pytest.mark.parametrize(
    ("limit_mib", "cpus", "expected"),
    [(2048, 16, 4), (2048, 2, 2), (256, 16, 1)],
)
def test_thread_limit_scales_with_address_space_cap(
    monkeypatch: pytest.MonkeyPatch, limit_mib: int, cpus: int, expected: int
) -> None:
    limit = limit_mib * 1024 * 1024
    monkeypatch.setattr(duckdb_action.resource, "getrlimit", lambda _: (limit, limit))
    monkeypatch.setattr(duckdb_action.os, "cpu_count", lambda: cpus)
    assert _thread_limit() == expected


def test_connect_applies_thread_limit(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(duckdb_action, "_thread_limit", lambda: 2)
    con = duckdb_action._connect()
    try:
        assert con.execute("SELECT current_setting('threads')").fetchone() == (2,)
    finally:
        con.close()


def test_thread_limit_none_without_resource_module(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(duckdb_action, "resource", None)
    assert _thread_limit() is None


@pytest.mark.skipif(sys.platform != "linux", reason="requires Linux glibc")
def test_execute_sql_with_s3_credentials_under_address_space_limit() -> None:
    con = duckdb_action._connect()
    try:
        try:
            con.execute("LOAD httpfs")
        except duckdb_action.duckdb.Error as exc:
            pytest.skip(f"httpfs is unavailable: {exc}")
    finally:
        con.close()

    code = """
import resource

limit = 1024 * 1024 * 1024
resource.setrlimit(resource.RLIMIT_AS, (limit, limit))

from tracecat_registry import secrets
from tracecat_registry.core.duckdb import execute_sql

token = secrets.set_context(
    {
        "AWS_ACCESS_KEY_ID": "test-access-key",
        "AWS_SECRET_ACCESS_KEY": "test-secret-key",
    }
)
try:
    for _ in range(5):
        assert execute_sql("SELECT 1") == [{"1": 1}]
finally:
    secrets.reset_context(token)
"""
    result = subprocess.run(
        [sys.executable, "-c", code],
        capture_output=True,
        text=True,
        timeout=60,
        check=False,
    )

    assert result.returncode == 0, result.stderr
    assert "terminate called" not in result.stderr
