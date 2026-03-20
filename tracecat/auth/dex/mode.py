from __future__ import annotations

from enum import StrEnum

from tracecat import config
from tracecat.auth.enums import AuthType


class MCPDexMode(StrEnum):
    SAML = "saml"
    OIDC = "oidc"
    BASIC = "basic"


def _explicit_mcp_dex_mode() -> MCPDexMode | None:
    match config.MCP_DEX_MODE:
        case MCPDexMode.SAML.value:
            return MCPDexMode.SAML
        case MCPDexMode.OIDC.value:
            return MCPDexMode.OIDC
        case MCPDexMode.BASIC.value:
            return MCPDexMode.BASIC
        case _:
            return None


def mcp_dex_saml_federation_configured() -> bool:
    return (
        AuthType.SAML in config.TRACECAT__AUTH_TYPES
        and bool(config.SAML_IDP_METADATA_URL)
        and bool(config.MCP_DEX_SAML_SSO_URL)
        and bool(config.MCP_DEX_SAML_CA_DATA or config.SAML_METADATA_CERT)
    )


def mcp_dex_oidc_federation_configured() -> bool:
    return (
        AuthType.OIDC in config.TRACECAT__AUTH_TYPES
        and bool(config.OIDC_ISSUER)
        and bool(config.OIDC_CLIENT_ID)
        and bool(config.OIDC_CLIENT_SECRET)
    )


def mcp_dex_basic_local_auth_configured() -> bool:
    return AuthType.BASIC in config.TRACECAT__AUTH_TYPES


def get_mcp_dex_mode() -> MCPDexMode | None:
    if explicit_mode := _explicit_mcp_dex_mode():
        return explicit_mode
    if mcp_dex_saml_federation_configured():
        return MCPDexMode.SAML
    if mcp_dex_oidc_federation_configured():
        return MCPDexMode.OIDC
    if mcp_dex_basic_local_auth_configured():
        return MCPDexMode.BASIC
    return None
