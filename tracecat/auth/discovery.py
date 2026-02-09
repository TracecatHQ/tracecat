"""Pre-auth domain discovery endpoint and routing hints."""

from __future__ import annotations

from enum import StrEnum
from typing import Final

from fastapi import APIRouter
from pydantic import EmailStr
from sqlalchemy import select

from tracecat import config
from tracecat.api.common import bootstrap_role
from tracecat.auth.enums import AuthType
from tracecat.core.schemas import Schema
from tracecat.db.dependencies import AsyncDBSession
from tracecat.db.models import OrganizationDomain
from tracecat.identifiers import OrganizationID
from tracecat.organization.domains import normalize_domain
from tracecat.service import BaseService
from tracecat.settings.service import get_setting

router = APIRouter(prefix="/auth", tags=["auth"])


class AuthDiscoveryMethod(StrEnum):
    """Authentication method hint for client-side routing."""

    BASIC = "basic"
    OIDC = "oidc"
    SAML = "saml"


class AuthDiscoverRequest(Schema):
    """Request payload for pre-auth discovery."""

    email: EmailStr


class AuthDiscoverResponse(Schema):
    """Pre-auth routing hint response."""

    method: AuthDiscoveryMethod


_SAML_SETTING_KEY: Final[str] = "saml_enabled"
_GOOGLE_OAUTH_SETTING_KEY: Final[str] = "oauth_google_enabled"
_BASIC_AUTH_SETTING_KEY: Final[str] = "auth_basic_enabled"


class AuthDiscoveryService(BaseService):
    """Resolve auth routing hints from email domain mappings."""

    service_name = "auth_discovery"

    async def discover(self, email: EmailStr) -> AuthDiscoverResponse:
        """Resolve the recommended login flow from an email address."""
        email_domain = self._extract_domain(email)
        org_id = await self._resolve_organization_id(email_domain)
        if org_id is None:
            return AuthDiscoverResponse(method=self._platform_fallback_method())

        return AuthDiscoverResponse(
            method=await self._organization_discovery_method(org_id)
        )

    async def _resolve_organization_id(self, domain: str) -> OrganizationID | None:
        normalized_domain = normalize_domain(domain).normalized_domain
        stmt = select(OrganizationDomain.organization_id).where(
            OrganizationDomain.normalized_domain == normalized_domain,
            OrganizationDomain.is_active.is_(True),
        )
        result = await self.session.execute(stmt)
        return result.scalar_one_or_none()

    async def _organization_discovery_method(
        self, org_id: OrganizationID
    ) -> AuthDiscoveryMethod:
        if await self._org_saml_enabled(org_id):
            return AuthDiscoveryMethod.SAML
        if await self._org_oidc_enabled(org_id):
            return AuthDiscoveryMethod.OIDC
        if await self._org_basic_enabled(org_id):
            return AuthDiscoveryMethod.BASIC
        return self._platform_fallback_method()

    async def _org_saml_enabled(self, org_id: OrganizationID) -> bool:
        if AuthType.SAML not in config.TRACECAT__AUTH_TYPES:
            return False
        value = await get_setting(
            _SAML_SETTING_KEY,
            role=bootstrap_role(org_id),
            session=self.session,
            default=True,
        )
        return bool(value)

    async def _org_oidc_enabled(self, org_id: OrganizationID) -> bool:
        if AuthType.OIDC in config.TRACECAT__AUTH_TYPES:
            return True
        if AuthType.GOOGLE_OAUTH in config.TRACECAT__AUTH_TYPES:
            value = await get_setting(
                _GOOGLE_OAUTH_SETTING_KEY,
                role=bootstrap_role(org_id),
                session=self.session,
                default=True,
            )
            return bool(value)
        return False

    async def _org_basic_enabled(self, org_id: OrganizationID) -> bool:
        if AuthType.BASIC not in config.TRACECAT__AUTH_TYPES:
            return False
        value = await get_setting(
            _BASIC_AUTH_SETTING_KEY,
            role=bootstrap_role(org_id),
            session=self.session,
            default=True,
        )
        return bool(value)

    @staticmethod
    def _extract_domain(email: str) -> str:
        _, _, domain = email.rpartition("@")
        return domain

    @staticmethod
    def _platform_fallback_method() -> AuthDiscoveryMethod:
        if (
            AuthType.OIDC in config.TRACECAT__AUTH_TYPES
            or AuthType.GOOGLE_OAUTH in config.TRACECAT__AUTH_TYPES
        ):
            return AuthDiscoveryMethod.OIDC
        if AuthType.BASIC in config.TRACECAT__AUTH_TYPES:
            return AuthDiscoveryMethod.BASIC
        if AuthType.SAML in config.TRACECAT__AUTH_TYPES:
            return AuthDiscoveryMethod.SAML
        return AuthDiscoveryMethod.BASIC


@router.post("/discover", response_model=AuthDiscoverResponse)
async def discover_auth_method(
    params: AuthDiscoverRequest,
    session: AsyncDBSession,
) -> AuthDiscoverResponse:
    """Return the next-step auth method for a given email."""
    service = AuthDiscoveryService(session)
    return await service.discover(params.email)
