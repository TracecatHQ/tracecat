"""Live LDAP regression tests backed by a real OpenLDAP container."""

from __future__ import annotations

import subprocess
import time
from collections.abc import Iterator
from contextlib import contextmanager
from typing import Any

import ldap3
import pytest
from ldap3.core.exceptions import LDAPException
from tracecat_registry._internal import secrets as registry_secrets
from tracecat_registry.integrations.ldap3 import search_entries

from tests import conftest as test_conftest


@contextmanager
def registry_secrets_sandbox(secrets: dict[str, str]):
    """Context manager that sets up the registry secrets context."""
    token = registry_secrets.set_context(secrets)
    try:
        yield
    finally:
        registry_secrets.reset_context(token)


LDAP_IMAGE = "osixia/openldap:1.5.0"
LDAP_DOMAIN = "example.com"
LDAP_BASE_DN = "dc=example,dc=com"
LDAP_ORG = "Tracecat"
LDAP_PASSWORD = "admin"
LDAP_BIND_DN = f"cn=admin,{LDAP_BASE_DN}"
LDAP_CONTAINER_NAME = f"test-ldap-{test_conftest.WORKER_ID}"
LDAP_PORT = 1389 + test_conftest.WORKER_OFFSET
LDAP_MAX_RETRIES = 5


def _run_docker(
    args: list[str], check: bool = True
) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        ["docker", *args],
        check=check,
        capture_output=True,
        text=True,
    )


@pytest.fixture(scope="session")
def live_ldap_server() -> Iterator[dict[str, Any]]:
    """Start an OpenLDAP container for the session."""

    _run_docker(["stop", LDAP_CONTAINER_NAME], check=False)
    _run_docker(["rm", LDAP_CONTAINER_NAME], check=False)

    run_args = [
        "run",
        "-d",
        "--name",
        LDAP_CONTAINER_NAME,
        "-p",
        f"{LDAP_PORT}:389",
        "-e",
        f"LDAP_ORGANISATION={LDAP_ORG}",
        "-e",
        f"LDAP_DOMAIN={LDAP_DOMAIN}",
        "-e",
        f"LDAP_ADMIN_PASSWORD={LDAP_PASSWORD}",
        LDAP_IMAGE,
    ]
    _run_docker(run_args)

    server = ldap3.Server("localhost", port=LDAP_PORT, get_info=ldap3.NONE)
    deadline = time.time() + 60
    while time.time() < deadline:
        try:
            conn = ldap3.Connection(
                server,
                user=LDAP_BIND_DN,
                password=LDAP_PASSWORD,
                auto_bind=True,
            )
            conn.unbind()
            break
        except LDAPException:
            time.sleep(1)
    else:
        logs = _run_docker(["logs", LDAP_CONTAINER_NAME], check=False)
        raise RuntimeError(
            f"LDAP server failed to start:\nSTDOUT:\n{logs.stdout}\nSTDERR:\n{logs.stderr}"
        )

    yield {
        "host": "localhost",
        "port": LDAP_PORT,
        "user": LDAP_BIND_DN,
        "password": LDAP_PASSWORD,
    }

    _run_docker(["stop", LDAP_CONTAINER_NAME], check=False)
    _run_docker(["rm", LDAP_CONTAINER_NAME], check=False)


def _assert_ldap_result(
    conn: ldap3.Connection, *, ok_codes: set[int], operation: str
) -> None:
    result_code = conn.result.get("result")
    if result_code not in ok_codes:
        raise RuntimeError(
            f"LDAP {operation} failed with result={result_code}, detail={conn.result}"
        )


def _seed_test_user_with_retry(live_ldap_server: dict[str, Any]) -> dict[str, str]:
    server = ldap3.Server(
        live_ldap_server["host"],
        port=live_ldap_server["port"],
        get_info=ldap3.NONE,
    )
    users_dn = f"ou=Users,{LDAP_BASE_DN}"
    user_dn = f"cn=Tracecat,{users_dn}"

    for attempt in range(LDAP_MAX_RETRIES):
        conn: ldap3.Connection | None = None
        try:
            conn = ldap3.Connection(
                server,
                user=live_ldap_server["user"],
                password=live_ldap_server["password"],
                auto_bind=True,
            )

            conn.add(users_dn, ["organizationalUnit", "top"], {"ou": "Users"})
            _assert_ldap_result(
                conn,
                ok_codes={0, 68},  # success, entry already exists
                operation="add users OU",
            )

            conn.search(user_dn, "(objectClass=*)")
            _assert_ldap_result(
                conn,
                ok_codes={0, 32},  # success, no such object
                operation="search user",
            )
            if conn.entries:
                conn.delete(user_dn)
                _assert_ldap_result(
                    conn,
                    ok_codes={0, 32},  # success, no such object
                    operation="delete user",
                )

            conn.add(
                user_dn,
                ["inetOrgPerson", "organizationalPerson", "person", "top"],
                {
                    "cn": "Tracecat",
                    "sn": "Test",
                    "uid": "tracecat.test",
                    "mail": "tracecat@example.com",
                },
            )
            _assert_ldap_result(conn, ok_codes={0}, operation="add user")
            return {"base_dn": users_dn, "filter": "(uid=tracecat.test)"}
        except (LDAPException, RuntimeError):
            if attempt == LDAP_MAX_RETRIES - 1:
                raise
            time.sleep(1 + attempt)
        finally:
            if conn is not None and conn.bound:
                conn.unbind()

    raise RuntimeError("Failed to seed LDAP test user after retries")


@pytest.fixture(scope="session")
def ldap_test_data(live_ldap_server: dict[str, Any]) -> dict[str, str]:
    """Ensure a known OU and user entry exists for each test."""
    return _seed_test_user_with_retry(live_ldap_server)


@pytest.fixture
def configure_ldap_secrets(live_ldap_server: dict[str, Any]):
    with registry_secrets_sandbox(
        {
            "LDAP_HOST": live_ldap_server["host"],
            "LDAP_PORT": str(live_ldap_server["port"]),
            "LDAP_USER": live_ldap_server["user"],
            "LDAP_PASSWORD": live_ldap_server["password"],
        }
    ):
        yield


def _run_search(
    ldap_data: dict[str, str],
) -> list[dict[str, Any]]:
    last_error: LDAPException | None = None
    for attempt in range(LDAP_MAX_RETRIES):
        try:
            return search_entries(
                search_base=ldap_data["base_dn"],
                search_filter=ldap_data["filter"],
            )
        except LDAPException as exc:
            last_error = exc
            if attempt == LDAP_MAX_RETRIES - 1:
                raise
            time.sleep(1 + attempt)

    if last_error is not None:
        raise last_error
    raise RuntimeError("Failed to execute LDAP search")


def test_live_search_all_attributes(
    ldap_test_data: dict[str, str], configure_ldap_secrets
) -> None:
    results = _run_search(ldap_test_data)
    assert results and results[0]["attributes"]["uid"] == ["tracecat.test"]


def test_live_search_returns_all_attributes(
    ldap_test_data: dict[str, str], configure_ldap_secrets
) -> None:
    results = _run_search(ldap_test_data)
    assert results
    attrs = results[0]["attributes"]
    # Verify that all expected attributes are present
    assert "uid" in attrs
    assert "mail" in attrs
    assert "cn" in attrs
    assert attrs["uid"] == ["tracecat.test"]
    assert attrs["mail"] == ["tracecat@example.com"]
