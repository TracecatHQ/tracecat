import base64
import secrets
import time
from dataclasses import asdict, dataclass
from typing import Annotated, Any

import defusedxml.ElementTree as ET
from diskcache import FanoutCache
from fastapi import APIRouter, Depends, Form, HTTPException, Request, Response, status
from fastapi_users.exceptions import UserAlreadyExists
from pydantic import BaseModel
from saml2 import BINDING_HTTP_POST
from saml2.client import Saml2Client
from saml2.config import Config as Saml2Config

from tracecat.api.common import bootstrap_role
from tracecat.auth.users import AuthBackendStrategyDep, UserManagerDep, auth_backend
from tracecat.config import (
    SAML_IDP_METADATA_URL,
    SAML_PUBLIC_ACS_URL,
    TRACECAT__PUBLIC_API_URL,
    XMLSEC_BINARY_PATH,
)
from tracecat.logger import logger
from tracecat.settings.service import get_setting

router = APIRouter(prefix="/auth/saml", tags=["auth"])

# Store SAML request IDs for response validation
_SAML_REQUEST_CACHE = FanoutCache(
    directory="/tmp/tracecat_saml_cache",
    timeout=30,
    shards=4,
)
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


async def create_saml_client() -> Saml2Client:
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

    saml_settings = {
        "strict": True,
        "entityid": TRACECAT__PUBLIC_API_URL,
        "xmlsec_binary": XMLSEC_BINARY_PATH,
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
                "allow_unsolicited": False,
                "authn_requests_signed": False,
                "want_assertions_signed": True,
                "want_response_signed": True,
                "want_assertions_or_response_signed": True,
                "only_use_keys_in_metadata": True,
                "validate_certificate": False,
            },
        },
        "metadata": {
            "remote": [
                {
                    "url": saml_idp_metadata_url,
                }
            ]
        },
        "accepted_time_diff": 3,
    }
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
) -> SAMLDatabaseLoginResponse:
    """Initiate SAML login flow"""

    # Generate a unique relay state
    relay_state = secrets.token_urlsafe(32)

    # Prepare the authentication request
    req_id, info = client.prepare_for_authenticate(relay_state=relay_state)

    # Store the request ID and relay state for validation later
    _SAML_REQUEST_CACHE.set(
        req_id,
        {
            "relay_state": relay_state,
            "timestamp": time.time(),
        },
        expire=_REQUEST_TIMEOUT,
    )

    logger.info(f"SAML login initiated with request ID: {req_id}")

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
    relay_state: str | None = Form(None, alias="RelayState"),
    user_manager: UserManagerDep,
    strategy: AuthBackendStrategyDep,
    client: Annotated[Saml2Client, Depends(create_saml_client)],
) -> Response:
    """Handle the SAML SSO response from the IdP post-authentication."""

    logger.info("SAML ACS endpoint called")

    try:
        decoded_response = base64.b64decode(saml_response).decode("utf-8")
        if (
            "<ds:Signature" not in decoded_response
            and "<Signature" not in decoded_response
        ):
            logger.error("SAML response does not contain a signature element")
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST, detail="Authentication failed"
            )

        if "InResponseTo=" not in decoded_response:
            logger.error("SAML response does not contain InResponseTo attribute")
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST, detail="Authentication failed"
            )

    except Exception as e:
        logger.error(f"Failed to decode SAML response: {e}")
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST, detail="Authentication failed"
        ) from e

    try:
        outstanding_queries = {}

        authn_response = client.parse_authn_request_response(
            saml_response,
            BINDING_HTTP_POST,
            outstanding=outstanding_queries,
        )
    except Exception as e:
        logger.error(f"SAML response parsing/validation failed: {str(e)}")
        if "Signature" in str(e) or "not signed" in str(e):
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST, detail="Authentication failed"
            ) from e
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST, detail="Authentication failed"
        ) from e

    if not authn_response:
        logger.error("SAML response validation failed - no response object")
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST, detail="Authentication failed"
        )

    if not hasattr(authn_response, "signature_check_result"):
        logger.error("SAML library did not perform signature validation")
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST, detail="Authentication failed"
        )

    response_signed = getattr(authn_response, "response_signed", False)
    assertions_signed = getattr(authn_response, "assertions_signed", False)

    if not response_signed and not assertions_signed:
        logger.error("SAML response and assertions are not signed")
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST, detail="Authentication failed"
        )

    try:
        raw_xml = getattr(authn_response, "_xmlstr", None)
        if raw_xml:
            if isinstance(raw_xml, bytes):
                raw_xml = raw_xml.decode("utf-8")

            if "<ds:Signature" not in raw_xml and "<Signature" not in raw_xml:
                logger.error("SAML response accepted by library but has no signature")
                raise HTTPException(
                    status_code=status.HTTP_400_BAD_REQUEST,
                    detail="Authentication failed",
                )
    except HTTPException:
        raise
    except Exception as e:
        logger.warning(f"Could not perform additional signature check: {e}")

    in_response_to = getattr(authn_response, "in_response_to", None)

    if not in_response_to or in_response_to == "":
        logger.error("SAML response missing or empty InResponseTo attribute")
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST, detail="Authentication failed"
        )

    if in_response_to == "_" or len(in_response_to) < 10:
        logger.error(f"SAML response has invalid InResponseTo: {in_response_to}")
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST, detail="Authentication failed"
        )

    stored_data = _SAML_REQUEST_CACHE.get(in_response_to)
    if not stored_data:
        logger.error(f"Unknown SAML request ID: {in_response_to}")
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST, detail="Authentication failed"
        )

    stored_relay_state = (
        stored_data["relay_state"] if isinstance(stored_data, dict) else None
    )
    if relay_state != stored_relay_state:
        logger.error("SAML relay state mismatch")
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST, detail="Authentication failed"
        )

    _SAML_REQUEST_CACHE.delete(in_response_to)

    logger.info(f"SAML response validated for request: {in_response_to}")

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
            f"Expected attribute 'email' in the SAML response, but got: {list(attributes.keys())}"
        )
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Authentication failed",
        )

    logger.info(f"SAML authentication successful for email: {email}")

    try:
        user = await user_manager.saml_callback(
            email=email,
            associate_by_email=True,
            is_verified_by_default=True,
        )
    except UserAlreadyExists as e:
        logger.error(f"User already exists: {e}")
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Authentication failed",
        ) from e

    if not user.is_active:
        logger.error(f"Inactive user attempted login: {email}")
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Authentication failed",
        )

    response = await auth_backend.login(strategy, user)
    await user_manager.on_after_login(user, request, response)
    return response
