"""SAML authentication flow and security gates.

This module implements the two public SAML endpoints:
- `GET /auth/saml/login`: starts SP-initiated SAML login.
- `POST /auth/saml/acs`: handles the IdP callback and finalizes login.

Tenant and organization model:
- Single-tenant: pre-auth SAML flows use the default organization.
- Multi-tenant: login must resolve an explicit org context before redirecting
  to the IdP. ACS derives org context from server-issued RelayState.

Configuration source model:
- SAML metadata URL is resolved per organization from settings.
- In single-tenant mode, `SAML_IDP_METADATA_URL` from env is intentionally
  preferred to preserve self-hosted compatibility when org settings are not
  configured yet.

Security and policy gates:
- `/login` is gated by auth-type checks, resolves the target org, and stores a
  one-time SAML request record with expiry.
- RelayState embeds org ID so ACS can select org-scoped configuration without
  trusting query/body parameters.
- `/acs` requires the `tracecat-ui` service role, validates RelayState exists
  and is not expired, validates `InResponseTo`, and deletes used request rows to
  prevent replay.
- Assertion email is selected from known claims and then validated against the
  org-domain allowlist. SAML sign-in is denied when an org has no active
  allowlisted domains, except for first-user superadmin bootstrap in the
  default organization.
- Membership enforcement is org-scoped: users can complete login when they are
  already members, have a pending invitation in the resolved org, or are the
  configured superadmin bootstrap account.

IDOR hardening note:
- ACS organization context is derived from server-issued RelayState and matched
  against stored request data. We do not trust caller-supplied org identifiers
  at callback time.
"""

import base64
import os
import secrets
import tempfile
import uuid
from contextlib import contextmanager
from dataclasses import asdict, dataclass
from datetime import UTC, datetime, timedelta
from typing import Any

import defusedxml.ElementTree as ET
from fastapi import APIRouter, Form, HTTPException, Request, Response, status
from fastapi_users.exceptions import UserAlreadyExists
from pydantic import BaseModel
from saml2 import BINDING_HTTP_POST
from saml2.client import Saml2Client
from saml2.config import Config as Saml2Config
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from tracecat.api.common import bootstrap_role, get_default_organization_id
from tracecat.auth.dependencies import ServiceRole, require_auth_type_enabled
from tracecat.auth.enums import AuthType
from tracecat.auth.org_context import resolve_auth_organization_id
from tracecat.auth.users import (
    AuthBackendStrategyDep,
    UserManagerDep,
    auth_backend,
)
from tracecat.config import (
    SAML_ACCEPTED_TIME_DIFF,
    SAML_ALLOW_UNSOLICITED,
    SAML_AUTHN_REQUESTS_SIGNED,
    SAML_CA_CERTS,
    SAML_IDP_METADATA_URL,
    SAML_PUBLIC_ACS_URL,
    SAML_SIGNED_ASSERTIONS,
    SAML_SIGNED_RESPONSES,
    SAML_VERIFY_SSL_ENTITY,
    SAML_VERIFY_SSL_METADATA,
    TRACECAT__AUTH_ALLOWED_DOMAINS,
    TRACECAT__AUTH_SUPERADMIN_EMAIL,
    TRACECAT__EE_MULTI_TENANT,
    TRACECAT__PUBLIC_API_URL,
    XMLSEC_BINARY_PATH,
)
from tracecat.db.dependencies import AsyncDBSession, AsyncDBSessionBypass
from tracecat.db.models import (
    OrganizationDomain,
    OrganizationInvitation,
    OrganizationMembership,
    SAMLRequestData,
    User,
)
from tracecat.identifiers import OrganizationID
from tracecat.invitations.enums import InvitationStatus
from tracecat.logger import logger
from tracecat.organization.domains import normalize_domain
from tracecat.settings.service import get_setting

router = APIRouter(prefix="/auth/saml", tags=["auth"])

# Request timeout in seconds
_REQUEST_TIMEOUT = 300


class SAMLDatabaseLoginResponse(BaseModel):
    redirect_url: str


@dataclass
class SAMLAttribute:
    """Represents a SAML attribute with its name, format, and value"""

    name: str
    value: str


class SAMLParser:
    """Parser for SAML AttributeStatement responses"""

    NAMESPACES = {
        "saml2": "urn:oasis:names:tc:SAML:2.0:assertion",
        "xs": "http://www.w3.org/2001/XMLSchema",
        "xsi": "http://www.w3.org/2001/XMLSchema-instance",
    }

    def __init__(self, xml_string: str):
        """Initialize parser with SAML XML string"""
        self.xml_string = xml_string.strip()
        self.attributes = None

    def _register_namespaces(self):
        """Register namespaces for proper XML handling"""
        pass

    def _extract_attribute(self, attribute_elem) -> SAMLAttribute:
        """Extract a single SAML attribute from an XML element"""

        name = attribute_elem.get("Name")
        value_elem = attribute_elem.find("saml2:AttributeValue", self.NAMESPACES)

        if not name:
            logger.error(
                f"SAML response failed: AttributeName for {attribute_elem} is empty"
            )
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail="Invalid authentication response",
            )

        if value_elem is None:
            logger.error(f"SAML response failed: AttributeValue for {name} not found")
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail="Invalid authentication response",
            )

        value_text = value_elem.text
        if value_text is None:
            logger.error(f"SAML response failed: AttributeValue for {name} is empty")
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail="Invalid authentication response",
            )

        return SAMLAttribute(name=name, value=value_text)

    def get_attribute_value(self, attribute_name: str) -> str:
        """Helper method to easily get an attribute value"""
        if self.attributes is None:
            self.attributes = self.parse_to_dict()
        return self.attributes.get(attribute_name, {}).get("value", "")

    def parse_to_dict(self) -> dict[str, Any]:
        """Parse SAML XML and return attributes as a dictionary"""
        self._register_namespaces()
        try:
            root = ET.fromstring(self.xml_string)
        except ET.ParseError as e:
            logger.error(f"SAML response parsing failed: {str(e)}")
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail="Invalid authentication response",
            ) from e

        # Find AttributeStatement
        attr_statement = root.find(".//saml2:AttributeStatement", self.NAMESPACES)
        if attr_statement is None:
            logger.error("SAML response failed: AttributeStatement not found")
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail="Invalid authentication response",
            )

        # Process all attributes
        attributes = {}
        for attr_elem in attr_statement.findall("saml2:Attribute", self.NAMESPACES):
            saml_attr = self._extract_attribute(attr_elem)
            attributes[saml_attr.name] = asdict(saml_attr)

        return attributes


@contextmanager
def ca_cert_tempfile(ca_cert_data: bytes):
    """Context manager for creating and cleaning up a temporary CA certificate file.

    Used for SSL/TLS transport layer certificate validation when fetching metadata
    over HTTPS from IdPs using self-signed SSL certificates.
    """
    ca_cert_file = None
    try:
        ca_cert_file = tempfile.NamedTemporaryFile(
            mode="wb", delete=False, suffix=".pem"
        )
        ca_cert_file.write(ca_cert_data)
        ca_cert_file.close()
        yield ca_cert_file.name
    except Exception:
        # Clean up on exception
        if ca_cert_file and os.path.exists(ca_cert_file.name):
            try:
                os.unlink(ca_cert_file.name)
            except OSError:
                pass
        raise
    finally:
        # Clean up after successful use
        if ca_cert_file and os.path.exists(ca_cert_file.name):
            try:
                os.unlink(ca_cert_file.name)
            except OSError:
                pass


@contextmanager
def metadata_cert_tempfile(metadata_cert_data: bytes):
    """Context manager for creating and cleaning up a temporary metadata certificate file.

    Used for SAML protocol message signature verification when the IdP uses self-signed
    certificates to sign SAML responses, assertions, and metadata documents.
    """
    metadata_cert_file = None
    try:
        metadata_cert_file = tempfile.NamedTemporaryFile(
            mode="wb", delete=False, suffix=".pem"
        )
        metadata_cert_file.write(metadata_cert_data)
        metadata_cert_file.close()
        yield metadata_cert_file.name
    except Exception:
        # Clean up on exception
        if metadata_cert_file and os.path.exists(metadata_cert_file.name):
            try:
                os.unlink(metadata_cert_file.name)
            except OSError:
                pass
        raise
    finally:
        # Clean up after successful use
        if metadata_cert_file and os.path.exists(metadata_cert_file.name):
            try:
                os.unlink(metadata_cert_file.name)
            except OSError:
                pass


def build_relay_state(organization_id: OrganizationID) -> str:
    """Encode organization context directly into RelayState."""
    token = secrets.token_urlsafe(32)
    return f"{organization_id}:{token}"


def parse_relay_state_org_id(relay_state: str) -> OrganizationID | None:
    """Extract org ID from RelayState if present."""
    prefix, _, _ = relay_state.partition(":")
    if not prefix:
        return None
    try:
        return uuid.UUID(prefix)
    except ValueError:
        return None


async def get_org_saml_metadata_url(
    session: AsyncSession, organization_id: OrganizationID
) -> str:
    """Load per-org SAML metadata URL with backward-compatible default.

    In single-tenant mode, prefer explicit environment configuration over
    encrypted DB settings to support self-hosted deployments and safe fallback.
    """
    # Single-tenant self-hosting compatibility:
    # prefer explicit env config to avoid forcing DB settings during bootstrap.
    if not TRACECAT__EE_MULTI_TENANT and SAML_IDP_METADATA_URL:
        return SAML_IDP_METADATA_URL

    # Multi-tenant hardening:
    # do not inherit global env metadata defaults across organizations.
    default_metadata_url = None if TRACECAT__EE_MULTI_TENANT else SAML_IDP_METADATA_URL
    value = await get_setting(
        "saml_idp_metadata_url",
        role=bootstrap_role(organization_id),
        session=session,
        default=default_metadata_url,
    )
    if not value:
        logger.error("SAML SSO metadata URL has not been configured")
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Authentication service not configured",
        )
    if not isinstance(value, str):
        logger.error("SAML SSO metadata URL is not a string")
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Invalid configuration",
        )
    return value


async def _get_active_org_domains(
    session: AsyncSession, organization_id: OrganizationID
) -> set[str]:
    domains_stmt = select(OrganizationDomain.normalized_domain).where(
        OrganizationDomain.organization_id == organization_id,
        OrganizationDomain.is_active.is_(True),
    )
    return set((await session.execute(domains_stmt)).scalars().all())


def _get_env_allowed_domains_for_saml() -> set[str]:
    """Return normalized env-domain allowlist for SAML checks."""
    normalized_domains: set[str] = set()
    for raw_domain in TRACECAT__AUTH_ALLOWED_DOMAINS:
        domain = raw_domain.strip().lower()
        if not domain:
            continue
        try:
            normalized_domains.add(normalize_domain(domain).normalized_domain)
        except ValueError:
            continue
    return normalized_domains


def _is_normalized_domain_allowed_for_org(
    *,
    normalized_domain: str,
    active_domains: set[str],
) -> bool:
    """Apply runtime SAML domain policy for a normalized email domain."""
    if active_domains:
        return normalized_domain in active_domains

    if TRACECAT__EE_MULTI_TENANT:
        return False

    env_allowed_domains = _get_env_allowed_domains_for_saml()
    if env_allowed_domains:
        return normalized_domain in env_allowed_domains
    return True


def _extract_candidate_emails(parser: SAMLParser) -> list[str]:
    """Extract candidate emails from known SAML attributes in priority order."""
    candidates = [
        parser.get_attribute_value("email"),
        # Okta
        parser.get_attribute_value(
            "urn:oasis:names:tc:SAML:1.1:nameid-format:emailAddress"
        ),
        # Microsoft Entra ID (prefer explicit email over UPN/name)
        parser.get_attribute_value(
            "http://schemas.xmlsoap.org/ws/2005/05/identity/claims/emailaddress"
        ),
        parser.get_attribute_value(
            "http://schemas.xmlsoap.org/ws/2005/05/identity/claims/name"
        ),
    ]
    seen: set[str] = set()
    deduped: list[str] = []
    for value in candidates:
        if not value:
            continue
        email = value.strip()
        if not email or email in seen:
            continue
        seen.add(email)
        deduped.append(email)
    return deduped


async def get_pending_org_invitation(
    session: AsyncSession, organization_id: OrganizationID, email: str
) -> OrganizationInvitation | None:
    """Return a pending, unexpired org invitation for the email if one exists."""
    normalized_email = email.strip().lower()
    if not normalized_email:
        return None
    statement = (
        select(OrganizationInvitation)
        .where(
            OrganizationInvitation.organization_id == organization_id,
            func.lower(OrganizationInvitation.email) == normalized_email,
            OrganizationInvitation.status == InvitationStatus.PENDING,
            OrganizationInvitation.expires_at > datetime.now(UTC),
        )
        .order_by(OrganizationInvitation.created_at.desc())
    )
    result = await session.execute(statement)
    return result.scalars().first()


async def _select_authorized_email(
    session: AsyncSession, organization_id: OrganizationID, candidates: list[str]
) -> tuple[str | None, OrganizationInvitation | None]:
    """Pick the best SAML email candidate allowed by org policy.

    Selection order:
    1. First-user superadmin bootstrap candidate (default org only).
    2. First allowlisted candidate that has a pending invitation.
    3. First allowlisted candidate without invitation (fallback).
    """
    active_domains = await _get_active_org_domains(session, organization_id)
    fallback_allowlisted_candidate: str | None = None

    for candidate in candidates:
        if await is_superadmin_saml_bootstrap_allowed_for_org(
            session, organization_id, candidate
        ):
            return candidate, None

        if "@" not in candidate:
            continue
        raw_domain = candidate.split("@", 1)[1].strip().lower()
        try:
            normalized_domain = normalize_domain(raw_domain).normalized_domain
        except ValueError:
            continue
        if not _is_normalized_domain_allowed_for_org(
            normalized_domain=normalized_domain,
            active_domains=active_domains,
        ):
            continue

        pending_invitation = await get_pending_org_invitation(
            session, organization_id, candidate
        )
        if pending_invitation is not None:
            return candidate, pending_invitation
        if fallback_allowlisted_candidate is None:
            fallback_allowlisted_candidate = candidate

    if fallback_allowlisted_candidate is not None:
        return fallback_allowlisted_candidate, None
    return None, None


def _is_superadmin_bootstrap_email(email: str) -> bool:
    superadmin_email = TRACECAT__AUTH_SUPERADMIN_EMAIL
    return bool(superadmin_email and email == superadmin_email)


async def is_first_superadmin_bootstrap_user(session: AsyncSession, email: str) -> bool:
    """Allow superadmin bootstrap bypass only for first-user registration."""
    if not _is_superadmin_bootstrap_email(email):
        return False

    users_count_stmt = select(func.count()).select_from(User)
    user_count = (await session.execute(users_count_stmt)).scalar_one()
    return user_count == 0


async def is_superadmin_saml_bootstrap_allowed_for_org(
    session: AsyncSession, organization_id: OrganizationID, email: str
) -> bool:
    """Allow superadmin SAML bootstrap only in default org and for first user."""
    if not await is_first_superadmin_bootstrap_user(session, email):
        return False
    try:
        default_org_id = await get_default_organization_id(session=session)
    except ValueError:
        return False
    return organization_id == default_org_id


def should_allow_saml_user_auto_provisioning(
    *,
    pending_invitation: OrganizationInvitation | None,
    is_first_superadmin_bootstrap: bool,
) -> bool:
    """Allow SAML user creation only for invitees and first superadmin bootstrap."""
    return pending_invitation is not None or is_first_superadmin_bootstrap


def should_allow_saml_org_access(
    *,
    has_existing_membership: bool,
    pending_invitation: OrganizationInvitation | None,
    is_first_superadmin_bootstrap: bool,
) -> bool:
    """Allow org access after SAML auth when at least one trusted path exists."""
    return (
        has_existing_membership
        or pending_invitation is not None
        or is_first_superadmin_bootstrap
    )


async def create_saml_client(
    saml_idp_metadata_url: str,
) -> Saml2Client:
    # Handle SSL certificate configuration for self-signed certificates
    saml_settings = {
        "strict": True,
        "entityid": TRACECAT__PUBLIC_API_URL,
        "xmlsec_binary": XMLSEC_BINARY_PATH,
        "verify_ssl_cert": SAML_VERIFY_SSL_ENTITY,
        "disable_ssl_certificate_validation": not SAML_VERIFY_SSL_METADATA,
        "service": {
            "sp": {
                "name": "tracecat_saml_sp",
                "description": "Tracecat SAML SSO Service Provider",
                "endpoints": {
                    "assertion_consumer_service": [
                        (
                            SAML_PUBLIC_ACS_URL,
                            BINDING_HTTP_POST,
                        ),
                    ],
                },
                "allow_unsolicited": SAML_ALLOW_UNSOLICITED,
                "authn_requests_signed": SAML_AUTHN_REQUESTS_SIGNED,
                "want_assertions_signed": SAML_SIGNED_ASSERTIONS,
                "want_response_signed": SAML_SIGNED_RESPONSES,
                "only_use_keys_in_metadata": True,
                "validate_certificate": True,
            },
        },
        "metadata": {
            "remote": [
                {
                    "url": saml_idp_metadata_url,
                }
            ]
        },
        "accepted_time_diff": SAML_ACCEPTED_TIME_DIFF,
    }

    # Configure CA certificate
    if SAML_CA_CERTS:
        try:
            # Decode base64 CA certificate and use context manager for temp file
            ca_cert_data = base64.b64decode(SAML_CA_CERTS)
            with ca_cert_tempfile(ca_cert_data) as ca_cert_path:
                logger.info(f"Using CA certificate file: {ca_cert_path}")

                # Create and configure SAML settings within the context
                config = Saml2Config()
                config.load(
                    {
                        **saml_settings,
                        "ca_certs": ca_cert_path,
                    }
                )
                client = Saml2Client(config)

                # Validate client
                if not client.metadata:
                    logger.error("SAML client has no metadata loaded")
                    raise HTTPException(
                        status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
                        detail="Authentication service unavailable",
                    )

                idp_entities = list(client.metadata.keys())
                if not idp_entities:
                    logger.error("No IdP entities found in metadata")
                    raise HTTPException(
                        status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
                        detail="Authentication service unavailable",
                    )

                logger.info(
                    f"SAML client initialized with IdP entities: {idp_entities}"
                )
                return client

        except Exception as e:
            logger.error(f"Failed to create CA certificate file: {e}")
            raise HTTPException(
                status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
                detail="SSL certificate configuration failed",
            ) from e
    else:
        # No CA certificate - proceed with regular configuration
        try:
            config = Saml2Config()
            config.load(saml_settings)
        except Exception as e:
            logger.error(f"Failed to load SAML configuration: {e}")
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail="Authentication service unavailable",
            ) from e

        client = Saml2Client(config)

        if not client.metadata:
            logger.error("SAML client has no metadata loaded")
            raise HTTPException(
                status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
                detail="Authentication service unavailable",
            )

        idp_entities = list(client.metadata.keys())
        if not idp_entities:
            logger.error("No IdP entities found in metadata")
            raise HTTPException(
                status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
                detail="Authentication service unavailable",
            )

        logger.info(f"SAML client initialized with IdP entities: {idp_entities}")
        return client


@router.get(
    "/login",
    name=f"saml:{auth_backend.name}.login",
    dependencies=[require_auth_type_enabled(AuthType.SAML)],
)
async def login(
    request: Request,
    db_session: AsyncDBSessionBypass,
) -> SAMLDatabaseLoginResponse:
    """Initiate SAML login flow"""
    # Org resolution is explicit in multi-tenant mode and default-org in
    # single-tenant mode. This keeps login org-scoped before we contact the IdP.
    organization_id = await resolve_auth_organization_id(request, session=db_session)
    saml_idp_metadata_url = await get_org_saml_metadata_url(db_session, organization_id)
    client = await create_saml_client(saml_idp_metadata_url)

    # RelayState carries org context so ACS can resolve org-scoped config without
    # trusting callback query/body org parameters.
    relay_state = build_relay_state(organization_id)

    # Prepare the authentication request
    req_id, info = client.prepare_for_authenticate(relay_state=relay_state)

    # Store the request ID and relay state for validation later
    expires_at = datetime.now(UTC) + timedelta(seconds=_REQUEST_TIMEOUT)
    saml_request = SAMLRequestData(
        id=req_id,
        relay_state=relay_state,
        expires_at=expires_at,
    )
    db_session.add(saml_request)
    await db_session.commit()

    logger.info(
        f"SAML login initiated with request ID: {req_id}, relay_state: {relay_state}, expires_at: {expires_at}"
    )

    try:
        headers = info["headers"]
        redirect_url = next(v for k, v in headers if k == "Location")
    except (KeyError, StopIteration) as e:
        logger.error(f"Redirect URL not found: {e}")
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Authentication failed",
        ) from e

    return SAMLDatabaseLoginResponse(redirect_url=redirect_url)


@router.post("/acs")
async def sso_acs(
    request: Request,
    *,
    saml_response: str = Form(..., alias="SAMLResponse"),
    relay_state: str = Form(..., alias="RelayState"),
    user_manager: UserManagerDep,
    strategy: AuthBackendStrategyDep,
    db_session: AsyncDBSession,
    role: ServiceRole,
) -> Response:
    """Handle the SAML SSO response from the IdP post-authentication."""

    if role.service_id != "tracecat-ui":
        logger.warning(
            f"SAML ACS accessed by unexpected service: '{role.service_id}'. "
            f"Expected 'tracecat-ui' for enhanced security."
        )
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Forbidden",
        )

    logger.info("SAML ACS endpoint called")
    logger.info(f"Configured SAML ACS URL: {SAML_PUBLIC_ACS_URL}")
    logger.info(f"Received RelayState: '{relay_state}' (type: {type(relay_state)})")

    organization_id = parse_relay_state_org_id(relay_state)
    if organization_id is None:
        # Backward-compatible fallback for legacy RelayState values that predate
        # org-prefixed RelayState format.
        logger.warning(
            "RelayState missing org prefix; using default organization fallback"
        )
        organization_id = await get_default_organization_id(db_session)

    relay_lookup_stmt = select(SAMLRequestData.id).where(
        SAMLRequestData.relay_state == relay_state,
        SAMLRequestData.expires_at > datetime.now(UTC),
    )
    matched_request_id = (
        await db_session.execute(relay_lookup_stmt)
    ).scalar_one_or_none()
    if matched_request_id is None:
        logger.error("Unknown or expired SAML relay state")
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Authentication failed",
        )

    # Load IdP metadata after RelayState validation so ACS config is tied to the
    # validated org context.
    saml_idp_metadata_url = await get_org_saml_metadata_url(db_session, organization_id)
    client = await create_saml_client(saml_idp_metadata_url)

    # Retrieve stored SAML requests to populate outstanding_queries.
    stmt = select(SAMLRequestData)
    result = await db_session.execute(stmt)
    stored_requests = result.scalars().all()

    # Build outstanding_queries dictionary for SAML library validation
    # Do NOT delete expired requests yet - defer until after SAML validation
    outstanding_queries = {}
    expired_requests = []

    for stored_request in stored_requests:
        # Check if request has expired but don't delete yet
        if datetime.now(UTC) > stored_request.expires_at:
            logger.info(f"Found expired SAML request: {stored_request.id}")
            expired_requests.append(stored_request)
            continue

        # Add to outstanding queries for validation
        outstanding_queries[stored_request.id] = stored_request.relay_state

    # Log outstanding queries for debugging
    logger.info(f"Outstanding SAML queries count: {len(outstanding_queries)}")

    # Clean up expired requests now (but don't commit yet)
    for expired_request in expired_requests:
        await db_session.delete(expired_request)

    try:
        authn_response = client.parse_authn_request_response(
            saml_response,
            BINDING_HTTP_POST,
            outstanding=outstanding_queries,
        )
    except Exception as e:
        # Sanitized error logging - no internal IDs exposed
        logger.error(
            f"SAML response parsing/validation failed: authentication error. "
            f"Outstanding queries count: {len(outstanding_queries)}, "
            f"Expired requests cleaned count: {len(expired_requests)}"
        )
        # Commit cleanup even on failure
        await db_session.commit()
        if "Signature" in str(e) or "not signed" in str(e):
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST, detail="Authentication failed"
            ) from e
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST, detail="Authentication failed"
        ) from e

    # Commit cleanup of expired requests after successful parsing
    await db_session.commit()

    if not authn_response:
        logger.error("SAML response validation failed - no response object")
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST, detail="Authentication failed"
        )

    in_response_to = getattr(authn_response, "in_response_to", None)
    logger.info("SAML InResponseTo extracted successfully")

    if not in_response_to or in_response_to == "":
        logger.error("SAML response missing or empty InResponseTo attribute")
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST, detail="Authentication failed"
        )

    if in_response_to == "_" or len(in_response_to) < 10:
        logger.error("SAML response has invalid InResponseTo format")
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST, detail="Authentication failed"
        )

    # Retrieve stored data from the database
    # Use a fresh query to ensure we have the most current data
    stmt = select(SAMLRequestData).where(SAMLRequestData.id == in_response_to)
    result = await db_session.execute(stmt)
    stored_request_data = result.scalar_one_or_none()

    if not stored_request_data:
        logger.error("Unknown or expired SAML request ID")
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST, detail="Authentication failed"
        )

    # Check if the request has expired
    if datetime.now(UTC) > stored_request_data.expires_at:
        logger.error("Expired SAML request ID")
        # Clean up expired entry
        await db_session.delete(stored_request_data)
        await db_session.commit()
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST, detail="Authentication failed"
        )

    stored_relay_state = stored_request_data.relay_state
    logger.info("Performing relay state validation")

    if relay_state != stored_relay_state:
        logger.error("SAML relay state mismatch")
        # Clean up entry even on relay state mismatch to prevent reuse
        await db_session.delete(stored_request_data)
        await db_session.commit()
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST, detail="Authentication failed"
        )

    # Delete the used request data to prevent replay attacks
    await db_session.delete(stored_request_data)
    await db_session.commit()

    logger.info("SAML response validated successfully")

    parser = SAMLParser(str(authn_response))
    candidate_emails = _extract_candidate_emails(parser)
    if not candidate_emails:
        attributes = parser.attributes or {}
        logger.error(
            f"Expected attribute 'email' in the SAML response, but got {len(attributes)} attributes"
        )
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Authentication failed",
        )

    email, pending_invitation = await _select_authorized_email(
        db_session, organization_id, candidate_emails
    )
    if email is None:
        logger.warning("SAML login denied by org domain allowlist/invitation checks")
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Authentication failed",
        )

    logger.info("SAML authentication successful")

    # Org-wide SAML auto-provisioning has been removed.
    # We only auto-provision user accounts for:
    # 1) The first-user superadmin bootstrap flow in the default org
    # 2) Users with a pending invitation in the target organization
    is_first_superadmin_bootstrap = await is_superadmin_saml_bootstrap_allowed_for_org(
        db_session, organization_id, email
    )
    allow_user_auto_provisioning = should_allow_saml_user_auto_provisioning(
        pending_invitation=pending_invitation,
        is_first_superadmin_bootstrap=is_first_superadmin_bootstrap,
    )

    try:
        user = await user_manager.saml_callback(
            email=email,
            organization_id=organization_id,
            associate_by_email=True,
            is_verified_by_default=True,
            allow_auto_provisioning=allow_user_auto_provisioning,
        )
    except UserAlreadyExists as e:
        logger.error("User already exists during SAML authentication")
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Authentication failed",
        ) from e

    if not user.is_active:
        logger.error("Inactive user attempted SAML login")
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Authentication failed",
        )

    # Ensure user can access this organization.
    membership_stmt = select(OrganizationMembership).where(
        OrganizationMembership.user_id == user.id,  # pyright: ignore[reportArgumentType]
        OrganizationMembership.organization_id == organization_id,
    )
    existing_membership = (
        await db_session.execute(membership_stmt)
    ).scalar_one_or_none()
    has_existing_membership = existing_membership is not None
    can_access_org = should_allow_saml_org_access(
        has_existing_membership=has_existing_membership,
        pending_invitation=pending_invitation,
        is_first_superadmin_bootstrap=is_first_superadmin_bootstrap,
    )
    if not can_access_org:
        logger.warning(
            "SAML login denied: user has no org membership and no pending invitation",
            email=email,
        )
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Authentication failed",
        )
    if (
        not has_existing_membership
        and pending_invitation is None
        and is_first_superadmin_bootstrap
    ):
        logger.info(
            "Allowing SAML login for first-user superadmin bootstrap without org membership",
            email=email,
        )

    response = await auth_backend.login(strategy, user)
    await user_manager.on_after_login(user, request, response)
    return response
