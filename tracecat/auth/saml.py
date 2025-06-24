import base64
import os
import secrets
import tempfile
from contextlib import contextmanager
from dataclasses import asdict, dataclass
from datetime import UTC, datetime, timedelta
from typing import Annotated, Any

import defusedxml.ElementTree as ET
from fastapi import APIRouter, Depends, Form, HTTPException, Request, Response, status
from fastapi_users.exceptions import UserAlreadyExists
from pydantic import BaseModel
from saml2 import BINDING_HTTP_POST
from saml2.client import Saml2Client
from saml2.config import Config as Saml2Config
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from tracecat.api.common import bootstrap_role
from tracecat.auth.credentials import RoleACL
from tracecat.auth.users import AuthBackendStrategyDep, UserManagerDep, auth_backend
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
    TRACECAT__APP_ENV,
    TRACECAT__PUBLIC_API_URL,
    XMLSEC_BINARY_PATH,
)
from tracecat.db.engine import get_async_session
from tracecat.db.schemas import SAMLRequestData
from tracecat.logger import logger
from tracecat.settings.service import get_setting
from tracecat.types.auth import Role

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


async def create_saml_client() -> Saml2Client:
    # Validate HTTPS requirement in production
    if TRACECAT__APP_ENV == "production":
        if not SAML_PUBLIC_ACS_URL.startswith("https://"):
            logger.error("SAML ACS URL must use HTTPS in production environment")
            raise HTTPException(
                status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
                detail="Authentication service misconfigured",
            )

    role = bootstrap_role()
    saml_idp_metadata_url = await get_setting(
        "saml_idp_metadata_url",
        role=role,
        default=SAML_IDP_METADATA_URL,
    )
    if not saml_idp_metadata_url:
        logger.error("SAML SSO metadata URL has not been configured")
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Authentication service not configured",
        )
    if not isinstance(saml_idp_metadata_url, str):
        logger.error("SAML SSO metadata URL is not a string")
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Invalid configuration",
        )

    # Handle SSL certificate configuration for self-signed certificates
    saml_settings = {
        "strict": True,
        "entityid": TRACECAT__PUBLIC_API_URL,
        "xmlsec_binary": XMLSEC_BINARY_PATH,
        "verify_ssl_cert": SAML_VERIFY_SSL_ENTITY,
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
                    "disable_ssl_certificate_validation": not SAML_VERIFY_SSL_METADATA,
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


@router.get("/login", name=f"saml:{auth_backend.name}.login")
async def login(
    client: Annotated[Saml2Client, Depends(create_saml_client)],
    db_session: Annotated[AsyncSession, Depends(get_async_session)],
) -> SAMLDatabaseLoginResponse:
    """Initiate SAML login flow"""

    # Generate a unique relay state
    relay_state = secrets.token_urlsafe(32)

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
    client: Annotated[Saml2Client, Depends(create_saml_client)],
    db_session: Annotated[AsyncSession, Depends(get_async_session)],
    role: Annotated[
        Role, RoleACL(allow_user=False, allow_service=True, require_workspace="no")
    ],
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

    # Retrieve all stored SAML requests to populate outstanding_queries
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

    email = (
        parser.get_attribute_value("email")
        # Okta
        or parser.get_attribute_value(
            "urn:oasis:names:tc:SAML:1.1:nameid-format:emailAddress"
        )
        # Microsoft Entra ID
        or parser.get_attribute_value(
            "http://schemas.xmlsoap.org/ws/2005/05/identity/claims/name"
        )
        or parser.get_attribute_value(
            "http://schemas.xmlsoap.org/ws/2005/05/identity/claims/emailaddress"
        )
    )

    if not email:
        attributes = parser.attributes or {}
        logger.error(
            f"Expected attribute 'email' in the SAML response, but got {len(attributes)} attributes"
        )
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Authentication failed",
        )

    logger.info("SAML authentication successful")

    try:
        user = await user_manager.saml_callback(
            email=email,
            associate_by_email=True,
            is_verified_by_default=True,
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

    response = await auth_backend.login(strategy, user)
    await user_manager.on_after_login(user, request, response)
    return response
