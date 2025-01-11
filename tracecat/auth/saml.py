import xml.etree.ElementTree as ET
from dataclasses import asdict, dataclass
from typing import Annotated, Any

from fastapi import APIRouter, Depends, Form, HTTPException, Request, Response, status
from fastapi_users.exceptions import UserAlreadyExists
from pydantic import BaseModel
from saml2 import BINDING_HTTP_POST
from saml2.client import Saml2Client
from saml2.config import Config as Saml2Config

from tracecat.auth.users import AuthBackendStrategyDep, UserManagerDep, auth_backend
from tracecat.config import (
    SAML_IDP_METADATA_URL,
    SAML_SP_ACS_URL,
    TRACECAT__PUBLIC_API_URL,
    XMLSEC_BINARY_PATH,
)
from tracecat.logger import logger
from tracecat.settings.service import get_setting

router = APIRouter(prefix="/auth/saml", tags=["auth"])


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
        self.attributes = None  # Store lazily parsed attributes

    def _register_namespaces(self):
        """Register namespaces for proper XML handling"""
        for prefix, uri in self.NAMESPACES.items():
            ET.register_namespace(prefix, uri)

    def _extract_attribute(self, attribute_elem: ET.Element) -> SAMLAttribute:
        """Extract a single SAML attribute from an XML element"""

        name = attribute_elem.get("Name")
        value_elem = attribute_elem.find("saml2:AttributeValue", self.NAMESPACES)

        if not name:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail=f"SAML response failed: AttributeName for {attribute_elem} is empty",
            )

        if value_elem is None:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail=f"SAML response failed: AttributeValue for {name} not found",
            )

        value_text = value_elem.text
        if value_text is None:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail=f"SAML response failed: AttributeValue for {name} is empty",
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
                detail="Failed to parse SAML response",
            ) from e

        # Find AttributeStatement
        attr_statement = root.find(".//saml2:AttributeStatement", self.NAMESPACES)
        if attr_statement is None:
            logger.error("SAML response failed: AttributeStatement not found")
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST, detail="Invalid SAML response"
            )

        # Process all attributes
        attributes = {}
        for attr_elem in attr_statement.findall("saml2:Attribute", self.NAMESPACES):
            saml_attr = self._extract_attribute(attr_elem)
            attributes[saml_attr.name] = asdict(saml_attr)

        return attributes


async def create_saml_client() -> Saml2Client:
    saml_idp_metadata_url = await get_setting(
        "saml_idp_metadata_url",
        default=SAML_IDP_METADATA_URL,
    )
    if not saml_idp_metadata_url:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="SAML SSO metadata URL has not been configured.",
        )
    if not isinstance(saml_idp_metadata_url, str):
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="SAML SSO metadata URL is not a string.",
        )

    saml_settings = {
        "strict": True,
        # The global unique identifier for this service provider
        "entityid": TRACECAT__PUBLIC_API_URL,
        "xmlsec_binary": XMLSEC_BINARY_PATH,
        # Service provider settings
        "service": {
            "sp": {
                "name": "tracecat_saml_sp",
                "description": "Tracecat SAML SSO Service Provider",
                "endpoints": {
                    "assertion_consumer_service": [
                        (SAML_SP_ACS_URL, BINDING_HTTP_POST),
                    ],
                },
                # Security settings
                "allow_unsolicited": True,  # If true, it allows the IdP to initiate the authentication
                "authn_requests_signed": False,  # Don't need to sign authn requests because we don't control the IdP
                "want_assertions_signed": True,  # We require the IdP to sign the assertions
                "want_response_signed": False,
            },
        },
        "metadata": {
            "remote": [
                {
                    "url": saml_idp_metadata_url,
                }
            ]
        },
    }
    try:
        config = Saml2Config()
        config.load(saml_settings)
    except Exception as e:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Failed to load SAML configuration",
        ) from e

    client = Saml2Client(config)
    return client


SamlClientDep = Annotated[Saml2Client, Depends(create_saml_client)]


@router.get("/login", name=f"saml:{auth_backend.name}.login")
async def login(client: SamlClientDep) -> SAMLDatabaseLoginResponse:
    _, info = client.prepare_for_authenticate()
    try:
        headers = info["headers"]
        # Select the IdP URL to send the AuthN request to
        redirect_url = next(v for k, v in headers if k == "Location")
    except (KeyError, StopIteration) as e:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Redirect URL not found in the SAML response.",
        ) from e
    # Return the redirect URL
    return SAMLDatabaseLoginResponse(redirect_url=redirect_url)


@router.post("/acs")
async def sso_acs(
    request: Request,
    *,
    saml_response: str = Form(..., alias="SAMLResponse"),
    user_manager: UserManagerDep,
    strategy: AuthBackendStrategyDep,
    client: SamlClientDep,
) -> Response:
    """Handle the SAML SSO response from the IdP post-authentication."""

    # Get email in the SAML response from the IdP
    authn_response = client.parse_authn_request_response(
        saml_response, BINDING_HTTP_POST
    )
    parser = SAMLParser(str(authn_response))

    # Try to get the email from SAML attributes
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
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=f"Expected attribute 'email' in the SAML response, but got: {list(attributes.keys())}",
        )

    # Try to get the user from the database
    try:
        user = await user_manager.saml_callback(
            email=email,
            associate_by_email=True,  # Assuming we want to associate by email
            is_verified_by_default=True,  # Assuming SAML-authenticated users are verified by default
        )
    except UserAlreadyExists as e:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="User already exists",
        ) from e

    if not user.is_active:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Bad credentials",
        )

    # Authenticate
    response = await auth_backend.login(strategy, user)
    await user_manager.on_after_login(user, request, response)
    return response
