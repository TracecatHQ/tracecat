from __future__ import annotations

from enum import StrEnum

from tracecat import config
from tracecat.auth.enums import AuthType


class MCPAuthMode(StrEnum):
    OIDC = "oidc"
    BASIC = "basic"
    SAML = "saml"


def _explicit_mcp_auth_mode() -> MCPAuthMode | None:
    match config.MCP_AUTH_MODE:
        case MCPAuthMode.OIDC.value:
            return MCPAuthMode.OIDC
        case MCPAuthMode.BASIC.value:
            return MCPAuthMode.BASIC
        case MCPAuthMode.SAML.value:
            return MCPAuthMode.SAML
        case _:
            return None


def mcp_oidc_federation_configured() -> bool:
    return (
        AuthType.OIDC in config.TRACECAT__AUTH_TYPES
        and bool(config.OIDC_ISSUER)
        and bool(config.OIDC_CLIENT_ID)
        and bool(config.OIDC_CLIENT_SECRET)
    )


def mcp_basic_local_auth_configured() -> bool:
    return AuthType.BASIC in config.TRACECAT__AUTH_TYPES


def mcp_saml_bridge_configured() -> bool:
    return AuthType.SAML in config.TRACECAT__AUTH_TYPES


def login_auth_type_enabled(auth_type: AuthType) -> bool:
    if explicit_mode := _explicit_mcp_auth_mode():
        return explicit_mode.value == auth_type.value
    return auth_type in config.TRACECAT__AUTH_TYPES


def get_login_auth_types() -> list[AuthType]:
    if explicit_mode := _explicit_mcp_auth_mode():
        return [AuthType(explicit_mode.value)]
    return sorted(config.TRACECAT__AUTH_TYPES, key=lambda auth_type: auth_type.value)


def get_mcp_auth_mode() -> MCPAuthMode | None:
    if explicit_mode := _explicit_mcp_auth_mode():
        return explicit_mode
    if mcp_oidc_federation_configured():
        return MCPAuthMode.OIDC
    if mcp_basic_local_auth_configured():
        return MCPAuthMode.BASIC
    if mcp_saml_bridge_configured():
        return MCPAuthMode.SAML
    return None
