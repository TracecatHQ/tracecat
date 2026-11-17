"""Tests for remote command target and error-pattern guards."""

import ipaddress
import socket

import pytest
from tracecat_registry._internal import remote_exec


@pytest.mark.parametrize(
    "address",
    [
        "127.0.0.1",
        "127.1.2.3",
        "0.0.0.0",
        "0.1.2.3",
        "169.254.169.254",
        "224.0.0.1",
        "240.0.0.1",
        "100.100.100.200",
        "168.63.129.16",
        "::1",
        "::",
        "fe80::1",
        "fd00:ec2::254",
        "::ffff:127.0.0.1",
        "::ffff:169.254.169.254",
        "::127.0.0.2",
        "64:ff9b::a9fe:a9fe",
        "2002:7f00:1::",
        "ff02::1",
    ],
)
def test_denies_loopback_link_local_and_metadata(address: str) -> None:
    assert remote_exec.is_denied_remote_address(ipaddress.ip_address(address))


@pytest.mark.parametrize(
    "address",
    [
        "10.0.0.5",
        "172.16.4.2",
        "192.168.1.10",
        "100.64.0.10",
        "fd12:3456::1",
        "203.0.113.7",
        "8.8.8.8",
        "2606:4700::1111",
    ],
)
def test_allows_private_and_public_hosts(address: str) -> None:
    assert not remote_exec.is_denied_remote_address(ipaddress.ip_address(address))


def _fake_getaddrinfo(*addresses: str):
    def getaddrinfo(host, port, *args, **kwargs):  # noqa: ANN001, ANN002, ANN003
        return [
            (socket.AF_INET, socket.SOCK_STREAM, 6, "", (address, port))
            for address in addresses
        ]

    return getaddrinfo


def test_resolve_rejects_any_denied_answer(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(
        remote_exec.socket,
        "getaddrinfo",
        _fake_getaddrinfo("198.51.100.4", "169.254.169.254"),
    )

    with pytest.raises(remote_exec.RemoteTargetDenied):
        remote_exec.resolve_remote_target("rebind.example.com", 22)


def test_resolve_returns_first_validated_address(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(
        remote_exec.socket,
        "getaddrinfo",
        _fake_getaddrinfo("10.1.2.3", "10.1.2.4"),
    )

    assert remote_exec.resolve_remote_target("server.internal", 22) == "10.1.2.3"


@pytest.mark.parametrize("host", ["localhost", "127.1", "[::1]", "fe80::1%eth0", ""])
def test_resolve_rejects_local_aliases(host: str) -> None:
    with pytest.raises(remote_exec.RemoteTargetDenied):
        remote_exec.resolve_remote_target(host, 22)


def test_find_error_pattern_is_literal() -> None:
    assert remote_exec.find_error_pattern([".*"], ["anything"]) is None
    assert remote_exec.find_error_pattern(["(a+)+$"], ["a" * 50_000]) is None
    assert remote_exec.find_error_pattern(["ERR"], ["ok", "ERR: x"]) == "ERR"


def test_output_tail_truncates() -> None:
    assert remote_exec.output_tail("x" * 5000).startswith("...")
    assert len(remote_exec.output_tail("x" * 5000)) == 2003
