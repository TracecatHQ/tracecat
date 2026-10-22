"""Schemas for workspace network tunnels."""

from __future__ import annotations

import ipaddress
import re
import uuid
from datetime import datetime
from enum import StrEnum
from typing import Annotated, Any, Self

from pydantic import (
    AnyHttpUrl,
    BaseModel,
    ConfigDict,
    Field,
    SecretStr,
    StringConstraints,
    field_validator,
    model_validator,
)

from tracecat.db.models import WorkspaceTunnel
from tracecat.identifiers import WorkspaceID

TunnelName = Annotated[
    str,
    StringConstraints(
        pattern=r"^[a-z0-9]([a-z0-9_-]*[a-z0-9])?$", min_length=1, max_length=63
    ),
]
"""Workspace-unique tunnel name used to reference the tunnel, e.g. 'onprem-splunk'."""

TunnelPort = Annotated[int, Field(ge=1, le=65535)]

_HOSTNAME_LABEL = re.compile(r"^(?!-)[a-z0-9-]{1,63}(?<!-)$")


class TunnelProvider(StrEnum):
    PANGOLIN = "pangolin"


class TunnelTarget(BaseModel):
    """A single private host and the TCP ports actions may reach through the tunnel."""

    model_config = ConfigDict(extra="forbid")

    host: str = Field(
        ...,
        min_length=1,
        max_length=253,
        description="Exact hostname or IP address. Wildcards and CIDR ranges are not allowed.",
    )
    ports: list[TunnelPort] = Field(..., min_length=1, max_length=64)

    @field_validator("host")
    @classmethod
    def validate_host(cls, value: str) -> str:
        host = value.strip().lower().rstrip(".")
        try:
            address = ipaddress.ip_address(host)
        except ValueError:
            pass
        else:
            return str(address)
        labels = host.split(".")
        if not host or not all(_HOSTNAME_LABEL.fullmatch(label) for label in labels):
            raise ValueError("host must be an exact hostname or IP address")
        return host

    @field_validator("ports")
    @classmethod
    def validate_ports(cls, value: list[int]) -> list[int]:
        if len(set(value)) != len(value):
            raise ValueError("ports must be unique")
        return sorted(value)


def _validate_endpoint(value: AnyHttpUrl) -> str:
    if value.scheme != "https":
        raise ValueError("endpoint must use https")
    if value.username or value.password:
        raise ValueError("endpoint must not contain credentials")
    if value.query or value.fragment:
        raise ValueError("endpoint must not contain a query or fragment")
    return str(value).rstrip("/")


def _validate_targets(targets: list[TunnelTarget]) -> list[TunnelTarget]:
    hosts = [target.host for target in targets]
    if len(set(hosts)) != len(hosts):
        raise ValueError("allowed_targets must not repeat a host")
    _reject_internal_hosts(targets)
    return targets


def _is_internal_host(host: str) -> bool:
    if host == "localhost" or host.endswith(".localhost"):
        return True
    try:
        address = ipaddress.ip_address(host)
    except ValueError:
        return False
    if isinstance(address, ipaddress.IPv6Address) and address.ipv4_mapped:
        address = address.ipv4_mapped
    return (
        address.is_loopback
        or address.is_link_local
        or address.is_unspecified
        or address.is_multicast
    )


def _reject_internal_hosts(targets: list[TunnelTarget] | None) -> None:
    """Reject loopback, link-local, and metadata targets on create and update.

    Gateways also refuse these at dial time, after DNS resolution.
    """
    for target in targets or ():
        if _is_internal_host(target.host):
            raise ValueError(
                f"{target.host} is a loopback, link-local, or metadata address"
            )


class TunnelCreate(BaseModel):
    model_config = ConfigDict(extra="forbid")

    name: TunnelName
    description: str | None = Field(default=None, max_length=1000)
    provider: TunnelProvider = TunnelProvider.PANGOLIN
    endpoint: AnyHttpUrl = Field(
        ...,
        description="Base URL of the Pangolin server, either Pangolin Cloud or self-hosted.",
    )
    client_id: str = Field(..., min_length=1, max_length=255)
    client_secret: SecretStr = Field(..., min_length=1, max_length=1024)
    allowed_targets: list[TunnelTarget] = Field(..., min_length=1, max_length=100)
    enabled: bool = True

    @field_validator("endpoint")
    @classmethod
    def validate_endpoint(cls, value: AnyHttpUrl) -> AnyHttpUrl:
        _validate_endpoint(value)
        return value

    @field_validator("allowed_targets")
    @classmethod
    def validate_allowed_targets(cls, value: list[TunnelTarget]) -> list[TunnelTarget]:
        return _validate_targets(value)


class TunnelUpdate(BaseModel):
    model_config = ConfigDict(extra="forbid")

    name: TunnelName | None = None
    description: str | None = Field(default=None, max_length=1000)
    endpoint: AnyHttpUrl | None = None
    client_id: str | None = Field(default=None, min_length=1, max_length=255)
    client_secret: SecretStr | None = Field(default=None, min_length=1, max_length=1024)
    allowed_targets: list[TunnelTarget] | None = Field(
        default=None, min_length=1, max_length=100
    )
    enabled: bool | None = None

    @field_validator("endpoint")
    @classmethod
    def validate_endpoint(cls, value: AnyHttpUrl | None) -> AnyHttpUrl | None:
        if value is not None:
            _validate_endpoint(value)
        return value

    @field_validator("allowed_targets")
    @classmethod
    def validate_allowed_targets(
        cls, value: list[TunnelTarget] | None
    ) -> list[TunnelTarget] | None:
        return _validate_targets(value) if value is not None else None

    @model_validator(mode="after")
    def reject_null_required_fields(self) -> Self:
        for field in (
            "name",
            "endpoint",
            "client_id",
            "client_secret",
            "allowed_targets",
            "enabled",
        ):
            if field in self.model_fields_set and getattr(self, field) is None:
                raise ValueError(
                    f"{field} cannot be null; omit the field to leave it unchanged"
                )
        return self


class TunnelRead(BaseModel):
    """Tunnel metadata. The client secret is write-only and never returned."""

    id: uuid.UUID
    workspace_id: WorkspaceID
    name: str
    description: str | None
    provider: TunnelProvider
    endpoint: str
    client_id: str
    allowed_targets: list[TunnelTarget]
    enabled: bool
    has_gateway_key: bool
    managed: bool
    created_at: datetime
    updated_at: datetime

    @classmethod
    def from_database(cls, obj: WorkspaceTunnel) -> TunnelRead:
        return cls(
            id=obj.id,
            workspace_id=obj.workspace_id,
            name=obj.name,
            description=obj.description,
            provider=TunnelProvider(obj.provider),
            endpoint=obj.endpoint,
            client_id=obj.client_id,
            allowed_targets=[
                TunnelTarget.model_validate(target) for target in obj.allowed_targets
            ],
            enabled=obj.enabled,
            has_gateway_key=obj.encrypted_gateway_key is not None,
            managed=obj.managed,
            created_at=obj.created_at,
            updated_at=obj.updated_at,
        )


class TunnelGatewayKeyRead(BaseModel):
    """A newly issued gateway key. It is only returned once, at issue time."""

    tunnel_id: uuid.UUID
    gateway_key: str = Field(
        ...,
        description="Configure this on the tunnel gateway. Issuing a new key revokes the old one.",
    )


class TunnelGatewayPolicyRead(BaseModel):
    """What a running gateway may currently do. Fetched with its gateway key."""

    tunnel_id: uuid.UUID
    workspace_id: WorkspaceID
    allowed_targets: list[TunnelTarget]
    managed: bool


class ManagedTunnelCreate(BaseModel):
    """Create a tunnel whose Pangolin site, machine client, and resources Tracecat manages."""

    model_config = ConfigDict(extra="forbid")

    name: TunnelName
    description: str | None = Field(default=None, max_length=1000)
    allowed_targets: list[TunnelTarget] = Field(..., min_length=1, max_length=100)

    @field_validator("allowed_targets")
    @classmethod
    def validate_allowed_targets(cls, value: list[TunnelTarget]) -> list[TunnelTarget]:
        return _validate_targets(value)


class ManagedTunnelCreated(BaseModel):
    """A new managed tunnel. The site credentials are only returned once."""

    tunnel: TunnelRead
    site_id: str
    site_secret: str
    site_endpoint: str
    site_install_command: str


class TunnelStatusRead(BaseModel):
    tunnel_id: uuid.UUID
    managed: bool
    site_online: bool | None
    gateway_ready: bool | None = None
    """Whether the Tracecat-run gateway has a ready pod. ``None`` when gateways
    are run outside Tracecat or the gateway has not been created yet."""


def serialize_targets(targets: list[TunnelTarget]) -> list[dict[str, Any]]:
    return [target.model_dump(mode="json") for target in targets]


def serialize_endpoint(endpoint: AnyHttpUrl) -> str:
    return _validate_endpoint(endpoint)


class TunnelUsageKind(StrEnum):
    MCP_INTEGRATION = "mcp_integration"
    AGENT_PRESET = "agent_preset"
    WORKFLOW = "workflow"


class TunnelUsage(BaseModel):
    """A workspace resource that routes traffic through a tunnel."""

    kind: TunnelUsageKind
    id: uuid.UUID
    name: str
    detail: str | None = Field(
        default=None,
        description="Where in the resource the tunnel is set, e.g. an action or tool",
    )


class TunnelUsageList(BaseModel):
    items: list[TunnelUsage]
