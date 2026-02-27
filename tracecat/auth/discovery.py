"""Pre-auth domain discovery endpoint and routing hints."""

from __future__ import annotations

from enum import StrEnum
from typing import Final
from urllib.parse import urlencode

from fastapi import APIRouter, HTTPException, status
from pydantic import EmailStr
from sqlalchemy import select

from tracecat import config
from tracecat.api.common import bootstrap_role, get_default_organization_id
from tracecat.auth.enums import AuthType
from tracecat.core.schemas import Schema
from tracecat.db.dependencies import AsyncDBSession
from tracecat.db.models import Organization, OrganizationDomain
from tracecat.exceptions import TracecatValidationError
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
    org: str | None = None


class AuthDiscoverResponse(Schema):
    """Pre-auth routing hint response."""

    method: AuthDiscoveryMethod
    next_url: str | None = None
    organization_slug: str | None = None


_SAML_SETTING_KEY: Final[str] = "saml_enabled"


class AuthDiscoveryService(BaseService):
    """Resolve auth routing hints from email domain mappings."""

    service_name = "auth_discovery"

    async def discover(
        self, email: EmailStr, org_slug: str | None = None
    ) -> AuthDiscoverResponse:
        """Resolve the recommended login flow from an email address."""
        if org_slug:
            org_resolution = await self._resolve_organization_by_slug(org_slug.strip())
            if org_resolution is None:
                raise TracecatValidationError("Invalid organization")
            org_id, resolved_org_slug = org_resolution
            method = await self._organization_discovery_method(org_id)
            return AuthDiscoverResponse(
                method=method,
                next_url=self._build_next_url(
                    method=method, email=str(email), org_slug=resolved_org_slug
                ),
                organization_slug=resolved_org_slug,
            )

        email_domain = self._extract_domain(email)
        resolution = await self._resolve_organization(email_domain)
        if resolution is None:
            method = await self._unmapped_domain_fallback_method()
            return AuthDiscoverResponse(
                method=method,
                next_url=self._build_next_url(method=method, email=str(email)),
            )

        org_id, org_slug = resolution
        method = await self._organization_discovery_method(org_id)
        return AuthDiscoverResponse(
            method=method,
            next_url=self._build_next_url(
                method=method, email=str(email), org_slug=org_slug
            ),
            organization_slug=org_slug,
        )

    async def _resolve_organization(
        self, domain: str
    ) -> tuple[OrganizationID, str] | None:
        normalized_domain = normalize_domain(domain).normalized_domain
        stmt = (
            select(OrganizationDomain.organization_id, Organization.slug)
            .join(Organization, Organization.id == OrganizationDomain.organization_id)
            .where(
                OrganizationDomain.normalized_domain == normalized_domain,
                OrganizationDomain.is_active.is_(True),
                Organization.is_active.is_(True),
            )
        )
        result = await self.session.execute(stmt)
        row = result.one_or_none()
        if row is None:
            return None
        return row[0], row[1]

    async def _resolve_organization_by_slug(
        self, slug: str
    ) -> tuple[OrganizationID, str] | None:
        stmt = select(Organization.id, Organization.slug).where(
            Organization.slug == slug,
            Organization.is_active.is_(True),
        )
        result = await self.session.execute(stmt)
        row = result.one_or_none()
        if row is None:
            return None
        return row[0], row[1]

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

    async def _org_oidc_enabled(self, _org_id: OrganizationID) -> bool:
        return (
            AuthType.OIDC in config.TRACECAT__AUTH_TYPES
            or AuthType.GOOGLE_OAUTH in config.TRACECAT__AUTH_TYPES
        )

    async def _org_basic_enabled(self, _org_id: OrganizationID) -> bool:
        return AuthType.BASIC in config.TRACECAT__AUTH_TYPES

    async def _unmapped_domain_fallback_method(self) -> AuthDiscoveryMethod:
        """Resolve fallback auth method when no org domain mapping is found."""
        if config.TRACECAT__EE_MULTI_TENANT:
            return self._platform_fallback_method()
        try:
            default_org_id = await get_default_organization_id(self.session)
        except ValueError:
            return self._platform_fallback_method()
        return await self._organization_discovery_method(default_org_id)

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

    @staticmethod
    def _build_next_url(
        *, method: AuthDiscoveryMethod, email: str, org_slug: str | None = None
    ) -> str | None:
        if method is not AuthDiscoveryMethod.SAML:
            return None
        # In multi-tenant mode the SAML login endpoint requires an org hint.
        # Return None so the frontend prompts for org selection instead of
        # sending the user to a URL that will 428.
        if not org_slug and config.TRACECAT__EE_MULTI_TENANT:
            return None
        params: dict[str, str] = {"email": email}
        if org_slug:
            params["org"] = org_slug
        query = urlencode(params)
        base = config.TRACECAT__PUBLIC_API_URL.rstrip("/")
        return f"{base}/auth/saml/login?{query}"


@router.post("/discover", response_model=AuthDiscoverResponse)
async def discover_auth_method(
    params: AuthDiscoverRequest,
    session: AsyncDBSession,
) -> AuthDiscoverResponse:
    """Return the next-step auth method for a given email."""
    service = AuthDiscoveryService(session)
    try:
        return await service.discover(params.email, params.org)
    except TracecatValidationError as e:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=str(e),
        ) from e
