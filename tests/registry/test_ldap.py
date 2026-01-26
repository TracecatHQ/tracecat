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


@pytest.fixture
def ldap_test_data(live_ldap_server: dict[str, Any]) -> dict[str, str]:
    """Ensure a known OU and user entry exists for each test."""

    server = ldap3.Server(
        live_ldap_server["host"],
        port=live_ldap_server["port"],
        get_info=ldap3.NONE,
    )
    users_dn = f"ou=Users,{LDAP_BASE_DN}"

    # Retry connection and OU creation with backoff - container may still be initializing
    max_retries = 5
    conn: ldap3.Connection | None = None
    for attempt in range(max_retries):
        try:
            conn = ldap3.Connection(
                server,
                user=live_ldap_server["user"],
                password=live_ldap_server["password"],
                auto_bind=True,
            )
            conn.add(users_dn, ["organizationalUnit", "top"], {"ou": "Users"})
            break
        except LDAPException:
            if attempt == max_retries - 1:
                raise
            time.sleep(1)

    assert conn is not None, "Failed to establish LDAP connection"
    user_dn = f"cn=Tracecat,{users_dn}"
    if conn.search(user_dn, "(objectClass=*)"):
        conn.delete(user_dn)

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
    conn.unbind()
    return {"base_dn": users_dn, "filter": "(uid=tracecat.test)"}


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
    return search_entries(
        search_base=ldap_data["base_dn"],
        search_filter=ldap_data["filter"],
    )


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
