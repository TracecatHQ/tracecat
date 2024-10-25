import tempfile
import xml.etree.ElementTree as ET
from contextlib import contextmanager
from dataclasses import asdict, dataclass
from typing import Annotated, Any

from fastapi import APIRouter, Depends, Form, HTTPException, Request, Response, status
from fastapi_users.exceptions import UserAlreadyExists
from pydantic import BaseModel
from saml2 import BINDING_HTTP_POST, BINDING_HTTP_REDIRECT
from saml2.client import Saml2Client
from saml2.config import Config as Saml2Config

from tracecat.auth.users import AuthBackendStrategyDep, UserManagerDep, auth_backend
from tracecat.config import (
    SAML_IDP_CERTIFICATE,
    SAML_IDP_ENTITY_ID,
    SAML_IDP_METADATA_URL,
    SAML_IDP_REDIRECT_URL,
    SAML_SP_ACS_URL,
    TRACECAT__PUBLIC_API_URL,
    XMLSEC_BINARY_PATH,
)
from tracecat.logger import logger

router = APIRouter(prefix="/auth/saml", tags=["auth"])


class SAMLDatabaseLoginResponse(BaseModel):
    redirect_url: str


@dataclass
class SAMLAttribute:
    """Represents a SAML attribute with its name, format, and value"""

    name: str
    value: str
    name_format: str = ""


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
        name = attribute_elem.get("Name", "")
        name_format = attribute_elem.get("NameFormat", "")

        # Get the attribute value
        value_elem = attribute_elem.find("saml2:AttributeValue", self.NAMESPACES)
        value = value_elem.text if value_elem is not None else ""

        return SAMLAttribute(name=name, value=value, name_format=name_format)

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
            ) from None

        # Find AttributeStatement
        attr_statement = root.find(".//saml2:AttributeStatement", self.NAMESPACES)
        if attr_statement is None:
            logger.error("SAML response failed: AttributeStatement not found")
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST, detail="Invalid SAML response"
            ) from None

        # Process all attributes
        attributes = {}
        for attr_elem in attr_statement.findall("saml2:Attribute", self.NAMESPACES):
            saml_attr = self._extract_attribute(attr_elem)
            attributes[saml_attr.name] = asdict(saml_attr)

        return attributes


@contextmanager
def generate_saml_metadata_file():
    """Generate a temporary SAML metadata file."""

    if not SAML_IDP_ENTITY_ID:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="SAML SSO entity ID has not been configured.",
        )

    if not SAML_IDP_REDIRECT_URL:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="SAML SSO redirect URL has not been configured.",
        )

    if not SAML_IDP_CERTIFICATE:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="SAML SSO certificate has not been configured.",
        )

    # Create the root element
    root = ET.Element(
        "EntityDescriptor",
        {
            "xmlns": "urn:oasis:names:tc:SAML:2.0:metadata",
            "xmlns:ds": "http://www.w3.org/2000/09/xmldsig#",
            "entityID": SAML_IDP_ENTITY_ID,
        },
    )

    # Create IDPSSODescriptor element
    idp_sso_descriptor = ET.SubElement(
        root,
        "IDPSSODescriptor",
        {"protocolSupportEnumeration": "urn:oasis:names:tc:SAML:2.0:protocol"},
    )

    # Add KeyDescriptor
    key_descriptor = ET.SubElement(idp_sso_descriptor, "KeyDescriptor", use="signing")
    key_info = ET.SubElement(key_descriptor, "ds:KeyInfo")
    x509_data = ET.SubElement(key_info, "ds:X509Data")
    x509_certificate = ET.SubElement(x509_data, "ds:X509Certificate")
    x509_certificate.text = SAML_IDP_CERTIFICATE

    # Add NameIDFormat
    name_id_format = ET.SubElement(idp_sso_descriptor, "NameIDFormat")
    name_id_format.text = "urn:oasis:names:tc:SAML:2.0:nameid-format:persistent"

    # Add SingleSignOnService
    ET.SubElement(
        idp_sso_descriptor,
        "SingleSignOnService",
        {"Binding": BINDING_HTTP_REDIRECT, "Location": SAML_IDP_REDIRECT_URL},
    )

    # Create a temporary file
    with tempfile.NamedTemporaryFile(mode="w+", suffix=".xml") as tmp_file:
        # Write the XML to the temporary file
        tree = ET.ElementTree(root)
        tree.write(tmp_file, encoding="unicode", xml_declaration=True)
        tmp_file.flush()
        tmp_file_path = tmp_file.name
        yield tmp_file_path


def create_saml_client() -> Saml2Client:
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
    }

    if SAML_IDP_METADATA_URL is None:
        with generate_saml_metadata_file() as tmp_metadata_path:
            # Add the local metadata file to the settings
            saml_settings["metadata"] = {"local": [tmp_metadata_path]}
            config = Saml2Config()
            config.load(saml_settings)
    else:
        # Save the cert to a temporary file
        with tempfile.NamedTemporaryFile(mode="w+", suffix=".crt") as tmp_file:
            tmp_file.write(SAML_IDP_CERTIFICATE)
            tmp_file.flush()
            saml_settings["metadata"] = {
                "remote": [
                    {
                        "url": SAML_IDP_METADATA_URL,
                        "cert": tmp_file.name,  # Path to cert
                    }
                ]
            }
            config = Saml2Config()
            config.load(saml_settings)

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
    except (KeyError, StopIteration):
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Redirect URL not found in the SAML response.",
        ) from None
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
    email = parser.get_attribute_value("email")

    # Try to get the user from the database
    try:
        user = await user_manager.saml_callback(
            email=email,
            associate_by_email=True,  # Assuming we want to associate by email
            is_verified_by_default=True,  # Assuming SAML-authenticated users are verified by default
        )
    except UserAlreadyExists:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="User already exists",
        ) from None

    if not user.is_active:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Bad credentials",
        ) from None

    # Authenticate
    response = await auth_backend.login(strategy, user)
    await user_manager.on_after_login(user, request, response)
    return response
