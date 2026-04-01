"""Internal OIDC issuer for MCP authentication.

This package implements a minimal OIDC authorization server that FastMCP's
``OIDCProxy`` uses as its upstream identity provider, replacing the
dependency on an external BYO OIDC IdP.

The issuer authenticates users via existing Tracecat session cookies and
issues short-lived JWT access tokens scoped to a single organization.
"""

from __future__ import annotations

from fastapi import APIRouter

from tracecat.mcp.oidc.endpoints import router as _endpoints_router

router = APIRouter(prefix="/mcp-oidc", tags=["mcp-oidc"])
router.include_router(_endpoints_router)
