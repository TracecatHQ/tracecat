import ipaddress
import uuid

import pytest
from fastapi import HTTPException
from pydantic import ValidationError

from tracecat.auth import ip_allowlist_enforcement
from tracecat.auth.ip_allowlist import (
    OrgIPAllowlist,
    compile_allowlist,
    normalize_cidrs,
    parse_cidr,
    parse_client_ip,
)
from tracecat.contexts import RequestAuditContext, ctx_request_audit
from tracecat.settings.schemas import (
    IPAllowlist,
    SecuritySettingsUpdate,
    ip_allowlist_cidrs,
    parse_stored_ip_allowlists,
)


@pytest.mark.parametrize(
    ("value", "expected"),
    [
        ("203.0.113.7", "203.0.113.7/32"),
        ("203.0.113.0/24", "203.0.113.0/24"),
        ("203.0.113.77/24", "203.0.113.0/24"),
        ("2001:db8::1", "2001:db8::1/128"),
        ("2001:db8::/32", "2001:db8::/32"),
        (" 203.0.113.7 ", "203.0.113.7/32"),
    ],
)
def test_parse_cidr_valid(value: str, expected: str) -> None:
    assert parse_cidr(value).with_prefixlen == expected


@pytest.mark.parametrize("value", ["", "not-an-ip", "203.0.113.999", "10.0.0.0/33"])
def test_parse_cidr_invalid(value: str) -> None:
    with pytest.raises(ValueError):
        parse_cidr(value)


def test_normalize_cidrs_dedupes_and_drops_blanks() -> None:
    assert normalize_cidrs(["203.0.113.7", "203.0.113.7/32", "", "  "]) == [
        "203.0.113.7/32"
    ]


def test_security_settings_update_rejects_invalid_cidr() -> None:
    with pytest.raises(ValidationError, match="Invalid IP address or CIDR"):
        SecuritySettingsUpdate(
            ip_allowlists=[IPAllowlist(name="Office", cidrs=["nope"])]
        )


def test_ip_allowlist_rejects_blank_name_and_empty_cidrs() -> None:
    with pytest.raises(ValidationError, match="Name cannot be blank"):
        IPAllowlist(name="   ", cidrs=["203.0.113.7"])
    with pytest.raises(ValidationError, match="At least one IP address"):
        IPAllowlist(name="Office", cidrs=["  "])


def test_security_settings_update_normalizes() -> None:
    params = SecuritySettingsUpdate(
        ip_allowlist_enabled=True,
        ip_allowlists=[
            IPAllowlist(
                name="  VPN ",
                description="  ",
                cidrs=["203.0.113.77/24", "203.0.113.0/24"],
            )
        ],
    )
    allowlist = params.ip_allowlists[0]
    assert allowlist.name == "VPN"
    assert allowlist.description is None
    assert allowlist.cidrs == ["203.0.113.0/24"]
    assert params.cidrs == ["203.0.113.0/24"]


def test_security_settings_update_rejects_duplicate_names() -> None:
    with pytest.raises(ValidationError, match="names must be unique"):
        SecuritySettingsUpdate(
            ip_allowlists=[
                IPAllowlist(name="VPN", cidrs=["203.0.113.7"]),
                IPAllowlist(name="vpn", cidrs=["198.51.100.7"]),
            ]
        )


def test_parse_stored_ip_allowlists() -> None:
    stored = [
        {"name": "VPN", "description": "Egress", "cidrs": ["203.0.113.0/24"]},
        {"name": "Office", "description": None, "cidrs": ["2001:db8::/32"]},
    ]
    allowlists = parse_stored_ip_allowlists(stored)
    assert [a.name for a in allowlists] == ["VPN", "Office"]
    assert ip_allowlist_cidrs(allowlists) == ["203.0.113.0/24", "2001:db8::/32"]
    assert parse_stored_ip_allowlists(None) == []
    assert parse_stored_ip_allowlists(["203.0.113.0/24"]) == []
    assert parse_stored_ip_allowlists([{"name": "x"}]) == []


def test_allowlist_match() -> None:
    allowlist = compile_allowlist(
        enabled=True, cidrs=["203.0.113.0/24", "2001:db8::/32", "garbage"]
    )
    assert len(allowlist.networks) == 2
    assert allowlist.enforced
    assert allowlist.match(ipaddress.ip_address("203.0.113.9")) is not None
    assert allowlist.match(ipaddress.ip_address("198.51.100.9")) is None
    assert allowlist.match(ipaddress.ip_address("2001:db8::9")) is not None
    assert allowlist.match(ipaddress.ip_address("2001:db9::9")) is None


def test_allowlist_not_enforced_when_disabled_or_empty() -> None:
    assert not compile_allowlist(enabled=False, cidrs=["203.0.113.0/24"]).enforced
    assert not compile_allowlist(enabled=True, cidrs=[]).enforced


def test_parse_client_ip() -> None:
    assert parse_client_ip(None) is None
    assert parse_client_ip("bogus") is None
    assert parse_client_ip("203.0.113.9") == ipaddress.ip_address("203.0.113.9")


def _set_client_ip(ip: str | None) -> None:
    ctx_request_audit.set(
        RequestAuditContext(client_ip=ip, user_agent=None, raw_user_agent=None)
    )


@pytest.fixture
def allowlist_stub(monkeypatch: pytest.MonkeyPatch):
    """Replace the cached DB loader with an in-memory allowlist."""
    state: dict[str, OrgIPAllowlist] = {}

    async def fake_loader(organization_id: uuid.UUID) -> OrgIPAllowlist:
        return state.get(
            str(organization_id), OrgIPAllowlist(enabled=False, networks=())
        )

    monkeypatch.setattr(ip_allowlist_enforcement, "get_org_ip_allowlist", fake_loader)
    return state


@pytest.mark.anyio
async def test_enforce_allows_when_no_allowlist(allowlist_stub) -> None:
    _set_client_ip("198.51.100.9")
    await ip_allowlist_enforcement.enforce_org_ip_allowlist(uuid.uuid4())


@pytest.mark.anyio
async def test_enforce_allows_matching_ip(allowlist_stub) -> None:
    org_id = uuid.uuid4()
    allowlist_stub[str(org_id)] = compile_allowlist(
        enabled=True, cidrs=["203.0.113.0/24"]
    )
    _set_client_ip("203.0.113.9")
    await ip_allowlist_enforcement.enforce_org_ip_allowlist(org_id)


@pytest.mark.anyio
async def test_enforce_denies_non_matching_ip(allowlist_stub) -> None:
    org_id = uuid.uuid4()
    allowlist_stub[str(org_id)] = compile_allowlist(
        enabled=True, cidrs=["203.0.113.0/24"]
    )
    _set_client_ip("198.51.100.9")
    with pytest.raises(HTTPException) as exc_info:
        await ip_allowlist_enforcement.enforce_org_ip_allowlist(org_id)
    assert exc_info.value.status_code == 403


@pytest.mark.anyio
async def test_enforce_denies_unresolvable_ip_when_enforced(allowlist_stub) -> None:
    org_id = uuid.uuid4()
    allowlist_stub[str(org_id)] = compile_allowlist(
        enabled=True, cidrs=["203.0.113.0/24"]
    )
    _set_client_ip(None)
    with pytest.raises(HTTPException):
        await ip_allowlist_enforcement.enforce_org_ip_allowlist(org_id)


@pytest.mark.anyio
async def test_enforce_bypass(allowlist_stub) -> None:
    org_id = uuid.uuid4()
    allowlist_stub[str(org_id)] = compile_allowlist(
        enabled=True, cidrs=["203.0.113.0/24"]
    )
    _set_client_ip("198.51.100.9")
    await ip_allowlist_enforcement.enforce_org_ip_allowlist(org_id, bypass=True)


@pytest.mark.anyio
async def test_enforce_disabled_allowlist_admits_everyone(allowlist_stub) -> None:
    org_id = uuid.uuid4()
    allowlist_stub[str(org_id)] = compile_allowlist(
        enabled=False, cidrs=["203.0.113.0/24"]
    )
    _set_client_ip("198.51.100.9")
    await ip_allowlist_enforcement.enforce_org_ip_allowlist(org_id)
