import base64
import time
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from defusedxml.common import EntitiesForbidden
from fastapi import HTTPException

from tracecat.auth.saml import (
    _SAML_REQUEST_CACHE,
    SAMLParser,
    create_saml_client,
    login,
    sso_acs,
)


class TestSAMLSecurity:
    """Test suite for SAML security vulnerabilities"""

    @pytest.fixture
    def mock_saml_client(self):
        """Create a mock SAML client"""
        client = MagicMock()
        client.metadata = {"https://idp.example.com": {}}
        return client

    @pytest.fixture
    def valid_saml_response_with_signature(self):
        """Create a valid SAML response with signature"""
        return base64.b64encode(
            b"""<samlp:Response xmlns:samlp="urn:oasis:names:tc:SAML:2.0:protocol"
                ID="_8e8dc5f69a98cc4c1ff3427e5ce34606fd672f91e6" Version="2.0"
                IssueInstant="2024-01-01T00:00:00Z" InResponseTo="_valid_request_id">
                <saml:Issuer xmlns:saml="urn:oasis:names:tc:SAML:2.0:assertion">https://idp.example.com</saml:Issuer>
                <ds:Signature xmlns:ds="http://www.w3.org/2000/09/xmldsig#">
                    <ds:SignedInfo>
                        <ds:CanonicalizationMethod Algorithm="http://www.w3.org/2001/10/xml-exc-c14n#"/>
                        <ds:SignatureMethod Algorithm="http://www.w3.org/2000/09/xmldsig#rsa-sha1"/>
                        <ds:Reference URI="#_8e8dc5f69a98cc4c1ff3427e5ce34606fd672f91e6">
                            <ds:Transforms>
                                <ds:Transform Algorithm="http://www.w3.org/2000/09/xmldsig#enveloped-signature"/>
                            </ds:Transforms>
                            <ds:DigestMethod Algorithm="http://www.w3.org/2000/09/xmldsig#sha1"/>
                            <ds:DigestValue>dGhpcyBpcyBhIGZha2UgZGlnZXN0</ds:DigestValue>
                        </ds:Reference>
                    </ds:SignedInfo>
                    <ds:SignatureValue>dGhpcyBpcyBhIGZha2Ugc2lnbmF0dXJl</ds:SignatureValue>
                </ds:Signature>
                <samlp:Status>
                    <samlp:StatusCode Value="urn:oasis:names:tc:SAML:2.0:status:Success"/>
                </samlp:Status>
                <saml:Assertion xmlns:saml="urn:oasis:names:tc:SAML:2.0:assertion"
                    ID="_assertion_id" Version="2.0" IssueInstant="2024-01-01T00:00:00Z">
                    <saml:Issuer>https://idp.example.com</saml:Issuer>
                    <saml:Subject>
                        <saml:NameID Format="urn:oasis:names:tc:SAML:1.1:nameid-format:emailAddress">test@example.com</saml:NameID>
                    </saml:Subject>
                    <saml:AttributeStatement>
                        <saml:Attribute Name="email">
                            <saml:AttributeValue>test@example.com</saml:AttributeValue>
                        </saml:Attribute>
                    </saml:AttributeStatement>
                </saml:Assertion>
            </samlp:Response>"""
        ).decode()

    @pytest.fixture
    def unsigned_saml_response(self):
        """Create an unsigned SAML response (attack vector)"""
        return base64.b64encode(
            b"""<samlp:Response xmlns:samlp="urn:oasis:names:tc:SAML:2.0:protocol"
                ID="_8e8dc5f69a98cc4c1ff3427e5ce34606fd672f91e6" Version="2.0"
                IssueInstant="2024-01-01T00:00:00Z" InResponseTo="_1234567890">
                <saml:Issuer xmlns:saml="urn:oasis:names:tc:SAML:2.0:assertion">https://idp.example.com</saml:Issuer>
                <samlp:Status>
                    <samlp:StatusCode Value="urn:oasis:names:tc:SAML:2.0:status:Success"/>
                </samlp:Status>
                <saml:Assertion xmlns:saml="urn:oasis:names:tc:SAML:2.0:assertion"
                    ID="_assertion_id" Version="2.0" IssueInstant="2024-01-01T00:00:00Z">
                    <saml:Issuer>https://idp.example.com</saml:Issuer>
                    <saml:Subject>
                        <saml:NameID Format="urn:oasis:names:tc:SAML:1.1:nameid-format:emailAddress">attacker@evil.com</saml:NameID>
                    </saml:Subject>
                    <saml:AttributeStatement>
                        <saml:Attribute Name="email">
                            <saml:AttributeValue>attacker@evil.com</saml:AttributeValue>
                        </saml:Attribute>
                    </saml:AttributeStatement>
                </saml:Assertion>
            </samlp:Response>"""
        ).decode()

    @pytest.fixture
    def saml_response_without_inresponseto(self):
        """Create a SAML response without InResponseTo (unsolicited)"""
        return base64.b64encode(
            b"""<samlp:Response xmlns:samlp="urn:oasis:names:tc:SAML:2.0:protocol"
                ID="_8e8dc5f69a98cc4c1ff3427e5ce34606fd672f91e6" Version="2.0"
                IssueInstant="2024-01-01T00:00:00Z">
                <saml:Issuer xmlns:saml="urn:oasis:names:tc:SAML:2.0:assertion">https://idp.example.com</saml:Issuer>
                <ds:Signature xmlns:ds="http://www.w3.org/2000/09/xmldsig#">
                    <ds:SignatureValue>fake_signature</ds:SignatureValue>
                </ds:Signature>
                <samlp:Status>
                    <samlp:StatusCode Value="urn:oasis:names:tc:SAML:2.0:status:Success"/>
                </samlp:Status>
                <saml:Assertion xmlns:saml="urn:oasis:names:tc:SAML:2.0:assertion">
                    <saml:AttributeStatement>
                        <saml:Attribute Name="email">
                            <saml:AttributeValue>attacker@evil.com</saml:AttributeValue>
                        </saml:Attribute>
                    </saml:AttributeStatement>
                </saml:Assertion>
            </samlp:Response>"""
        ).decode()

    @pytest.mark.anyio
    async def test_unsigned_saml_response_rejected(
        self, mock_saml_client, unsigned_saml_response
    ):
        """Test that unsigned SAML responses are rejected"""
        mock_request = MagicMock()
        mock_user_manager = AsyncMock()
        mock_strategy = MagicMock()

        # Test should fail at the signature check
        with pytest.raises(HTTPException) as exc_info:
            await sso_acs(
                request=mock_request,
                saml_response=unsigned_saml_response,
                relay_state="test_relay_state",
                user_manager=mock_user_manager,
                strategy=mock_strategy,
                client=mock_saml_client,
            )

        assert exc_info.value.status_code == 400
        assert exc_info.value.detail == "Authentication failed"

    @pytest.mark.anyio
    async def test_saml_response_without_inresponseto_rejected(
        self, mock_saml_client, saml_response_without_inresponseto
    ):
        """Test that SAML responses without InResponseTo are rejected"""
        mock_request = MagicMock()
        mock_user_manager = AsyncMock()
        mock_strategy = MagicMock()

        # Test should fail at the InResponseTo check
        with pytest.raises(HTTPException) as exc_info:
            await sso_acs(
                request=mock_request,
                saml_response=saml_response_without_inresponseto,
                relay_state="test_relay_state",
                user_manager=mock_user_manager,
                strategy=mock_strategy,
                client=mock_saml_client,
            )

        assert exc_info.value.status_code == 400
        assert exc_info.value.detail == "Authentication failed"

    @pytest.mark.anyio
    async def test_replay_attack_prevention(
        self, mock_saml_client, valid_saml_response_with_signature
    ):
        """Test that replay attacks are prevented"""
        mock_request = MagicMock()
        mock_user_manager = AsyncMock()
        mock_strategy = MagicMock()

        # Set up a valid request in cache
        request_id = "_valid_request_id"
        relay_state = "test_relay_state"
        _SAML_REQUEST_CACHE.set(
            request_id,
            {"relay_state": relay_state, "timestamp": time.time()},
            expire=300,
        )

        # Mock the parse_authn_request_response to return a valid response
        mock_response = MagicMock()
        mock_response.in_response_to = request_id
        mock_response.signature_check_result = True
        mock_response.response_signed = True
        mock_response.assertions_signed = True
        mock_response._xmlstr = f"""<samlp:Response xmlns:samlp="urn:oasis:names:tc:SAML:2.0:protocol"
            ID="_8e8dc5f69a98cc4c1ff3427e5ce34606fd672f91e6" Version="2.0"
            IssueInstant="2024-01-01T00:00:00Z" InResponseTo="{request_id}">
            <saml:Issuer xmlns:saml="urn:oasis:names:tc:SAML:2.0:assertion">https://idp.example.com</saml:Issuer>
            <ds:Signature xmlns:ds="http://www.w3.org/2000/09/xmldsig#">
                <ds:SignedInfo>
                    <ds:CanonicalizationMethod Algorithm="http://www.w3.org/2001/10/xml-exc-c14n#"/>
                    <ds:SignatureMethod Algorithm="http://www.w3.org/2000/09/xmldsig#rsa-sha1"/>
                    <ds:Reference URI="#_8e8dc5f69a98cc4c1ff3427e5ce34606fd672f91e6">
                        <ds:Transforms>
                            <ds:Transform Algorithm="http://www.w3.org/2000/09/xmldsig#enveloped-signature"/>
                        </ds:Transforms>
                        <ds:DigestMethod Algorithm="http://www.w3.org/2000/09/xmldsig#sha1"/>
                        <ds:DigestValue>dGhpcyBpcyBhIGZha2UgZGlnZXN0</ds:DigestValue>
                    </ds:Reference>
                </ds:SignedInfo>
                <ds:SignatureValue>dGhpcyBpcyBhIGZha2Ugc2lnbmF0dXJl</ds:SignatureValue>
            </ds:Signature>
            <samlp:Status>
                <samlp:StatusCode Value="urn:oasis:names:tc:SAML:2.0:status:Success"/>
            </samlp:Status>
            <saml:Assertion xmlns:saml="urn:oasis:names:tc:SAML:2.0:assertion"
                ID="_assertion_id" Version="2.0" IssueInstant="2024-01-01T00:00:00Z">
                <saml:Issuer>https://idp.example.com</saml:Issuer>
                <saml:Subject>
                    <saml:NameID Format="urn:oasis:names:tc:SAML:1.1:nameid-format:emailAddress">test@example.com</saml:NameID>
                </saml:Subject>
                <saml:AttributeStatement>
                    <saml:Attribute Name="email">
                        <saml:AttributeValue>test@example.com</saml:AttributeValue>
                    </saml:Attribute>
                </saml:AttributeStatement>
            </saml:Assertion>
        </samlp:Response>"""
        mock_response.configure_mock(__str__=lambda self: self._xmlstr)

        mock_saml_client.parse_authn_request_response.return_value = mock_response

        # First attempt should succeed (mocking the full flow)
        mock_user = MagicMock()
        mock_user.is_active = True
        mock_user_manager.saml_callback.return_value = mock_user

        mock_auth_response = MagicMock()
        with patch(
            "tracecat.auth.saml.auth_backend.login", return_value=mock_auth_response
        ):
            response = await sso_acs(
                request=mock_request,
                saml_response=base64.b64encode(mock_response._xmlstr.encode()).decode(),
                relay_state=relay_state,
                user_manager=mock_user_manager,
                strategy=mock_strategy,
                client=mock_saml_client,
            )
            assert response == mock_auth_response

        # Second attempt with same response should fail (replay attack)
        with pytest.raises(HTTPException) as exc_info:
            await sso_acs(
                request=mock_request,
                saml_response=base64.b64encode(mock_response._xmlstr.encode()).decode(),
                relay_state=relay_state,
                user_manager=mock_user_manager,
                strategy=mock_strategy,
                client=mock_saml_client,
            )

        assert exc_info.value.status_code == 400
        assert exc_info.value.detail == "Authentication failed"

    @pytest.mark.anyio
    async def test_relay_state_validation(
        self, mock_saml_client, valid_saml_response_with_signature
    ):
        """Test that relay state is properly validated for CSRF protection"""
        mock_request = MagicMock()
        mock_user_manager = AsyncMock()
        mock_strategy = MagicMock()

        # Set up a valid request in cache
        request_id = "_valid_request_id"
        correct_relay_state = "correct_relay_state"
        _SAML_REQUEST_CACHE.set(
            request_id,
            {"relay_state": correct_relay_state, "timestamp": time.time()},
            expire=300,
        )

        # Mock the parse_authn_request_response
        mock_response = MagicMock()
        mock_response.in_response_to = request_id
        mock_response.signature_check_result = True
        mock_response.response_signed = True
        mock_response.assertions_signed = True
        mock_response._xmlstr = valid_saml_response_with_signature.encode()

        mock_saml_client.parse_authn_request_response.return_value = mock_response

        # Test with wrong relay state
        with pytest.raises(HTTPException) as exc_info:
            await sso_acs(
                request=mock_request,
                saml_response=valid_saml_response_with_signature,
                relay_state="wrong_relay_state",  # Wrong relay state
                user_manager=mock_user_manager,
                strategy=mock_strategy,
                client=mock_saml_client,
            )

        assert exc_info.value.status_code == 400
        assert exc_info.value.detail == "Authentication failed"

    @pytest.mark.anyio
    async def test_invalid_inresponseto_rejected(
        self, mock_saml_client, valid_saml_response_with_signature
    ):
        """Test that invalid InResponseTo values are rejected"""
        mock_request = MagicMock()
        mock_user_manager = AsyncMock()
        mock_strategy = MagicMock()

        # Test various invalid InResponseTo values
        invalid_values = ["", "_", "_short", None]

        for invalid_value in invalid_values:
            mock_response = MagicMock()
            mock_response.in_response_to = invalid_value
            mock_response.signature_check_result = True
            mock_response.response_signed = True
            mock_response.assertions_signed = True
            mock_response._xmlstr = valid_saml_response_with_signature.encode()

            mock_saml_client.parse_authn_request_response.return_value = mock_response

            with pytest.raises(HTTPException) as exc_info:
                await sso_acs(
                    request=mock_request,
                    saml_response=valid_saml_response_with_signature,
                    relay_state="test_relay_state",
                    user_manager=mock_user_manager,
                    strategy=mock_strategy,
                    client=mock_saml_client,
                )

            assert exc_info.value.status_code == 400
            assert exc_info.value.detail == "Authentication failed"

    @pytest.mark.anyio
    async def test_expired_request_rejected(
        self, mock_saml_client, valid_saml_response_with_signature
    ):
        """Test that responses for expired requests are rejected"""
        mock_request = MagicMock()
        mock_user_manager = AsyncMock()
        mock_strategy = MagicMock()

        request_id = "_expired_request_id"

        # Mock response with expired request ID
        mock_response = MagicMock()
        mock_response.in_response_to = request_id
        mock_response.signature_check_result = True
        mock_response.response_signed = True
        mock_response.assertions_signed = True
        mock_response._xmlstr = valid_saml_response_with_signature.encode()

        mock_saml_client.parse_authn_request_response.return_value = mock_response

        # Request ID not in cache (expired or never existed)
        with pytest.raises(HTTPException) as exc_info:
            await sso_acs(
                request=mock_request,
                saml_response=valid_saml_response_with_signature,
                relay_state="test_relay_state",
                user_manager=mock_user_manager,
                strategy=mock_strategy,
                client=mock_saml_client,
            )

        assert exc_info.value.status_code == 400
        assert exc_info.value.detail == "Authentication failed"

    @pytest.mark.anyio
    async def test_saml_client_initialization_failures(self):
        """Test various SAML client initialization failure scenarios"""

        # Test with no metadata URL
        with patch("tracecat.auth.saml.get_setting", return_value=None):
            with pytest.raises(HTTPException) as exc_info:
                await create_saml_client()
            assert exc_info.value.status_code == 400
            assert exc_info.value.detail == "Authentication service not configured"

        # Test with invalid metadata URL type
        with patch("tracecat.auth.saml.get_setting", return_value=123):
            with pytest.raises(HTTPException) as exc_info:
                await create_saml_client()
            assert exc_info.value.status_code == 400
            assert exc_info.value.detail == "Invalid configuration"

    def test_saml_parser_security(self):
        """Test SAMLParser against various attack vectors"""

        # Test XXE attack prevention (defusedxml should handle this)
        xxe_payload = """<?xml version="1.0" encoding="UTF-8"?>
        <!DOCTYPE foo [
        <!ENTITY xxe SYSTEM "file:///etc/passwd">
        ]>
        <saml2:AttributeStatement xmlns:saml2="urn:oasis:names:tc:SAML:2.0:assertion">
            <saml2:Attribute Name="email">
                <saml2:AttributeValue>&xxe;</saml2:AttributeValue>
            </saml2:Attribute>
        </saml2:AttributeStatement>"""

        parser = SAMLParser(xxe_payload)
        # defusedxml should raise EntitiesForbidden for XXE attacks
        with pytest.raises(EntitiesForbidden) as exc_info:
            parser.parse_to_dict()
        assert exc_info.value.name == "xxe"
        assert exc_info.value.sysid == "file:///etc/passwd"

        # Test missing AttributeStatement
        invalid_xml = (
            """<root xmlns:saml2="urn:oasis:names:tc:SAML:2.0:assertion"></root>"""
        )
        parser = SAMLParser(invalid_xml)
        with pytest.raises(HTTPException) as exc_info:
            parser.parse_to_dict()
        assert exc_info.value.status_code == 400
        assert exc_info.value.detail == "Invalid authentication response"

        # Test missing attribute name
        missing_name_xml = """<saml2:AttributeStatement xmlns:saml2="urn:oasis:names:tc:SAML:2.0:assertion">
            <saml2:Attribute>
                <saml2:AttributeValue>test@example.com</saml2:AttributeValue>
            </saml2:Attribute>
        </saml2:AttributeStatement>"""
        parser = SAMLParser(missing_name_xml)
        with pytest.raises(HTTPException) as exc_info:
            parser.parse_to_dict()
        assert exc_info.value.status_code == 400
        assert exc_info.value.detail == "Invalid authentication response"

    @pytest.mark.anyio
    async def test_login_generates_secure_relay_state(self, mock_saml_client):
        """Test that login generates cryptographically secure relay state"""
        mock_saml_client.prepare_for_authenticate.return_value = (
            "_request_id",
            {"headers": [("Location", "https://idp.example.com/sso")]},
        )

        await login(client=mock_saml_client)

        # Verify request was stored in cache
        stored_data = _SAML_REQUEST_CACHE.get("_request_id")
        assert stored_data is not None
        assert isinstance(stored_data, dict)
        assert "relay_state" in stored_data
        assert "timestamp" in stored_data

        # Verify relay state is sufficiently random (at least 32 chars from token_urlsafe)
        assert len(stored_data["relay_state"]) >= 32

    @pytest.mark.anyio
    async def test_inactive_user_rejected(
        self, mock_saml_client, valid_saml_response_with_signature
    ):
        """Test that inactive users cannot authenticate"""
        mock_request = MagicMock()
        mock_user_manager = AsyncMock()
        mock_strategy = MagicMock()

        # Set up valid request in cache
        request_id = "_valid_request_id"
        relay_state = "test_relay_state"
        _SAML_REQUEST_CACHE.set(
            request_id,
            {"relay_state": relay_state, "timestamp": time.time()},
            expire=300,
        )

        # Mock valid SAML response
        mock_response = MagicMock()
        mock_response.in_response_to = request_id
        mock_response.signature_check_result = True
        mock_response.response_signed = True
        mock_response.assertions_signed = True
        mock_response._xmlstr = valid_saml_response_with_signature.encode()
        mock_response.configure_mock(
            __str__=lambda self: base64.b64decode(
                valid_saml_response_with_signature
            ).decode()
        )

        mock_saml_client.parse_authn_request_response.return_value = mock_response

        # Mock inactive user
        mock_user = MagicMock()
        mock_user.is_active = False
        mock_user_manager.saml_callback.return_value = mock_user

        with pytest.raises(HTTPException) as exc_info:
            await sso_acs(
                request=mock_request,
                saml_response=valid_saml_response_with_signature,
                relay_state=relay_state,
                user_manager=mock_user_manager,
                strategy=mock_strategy,
                client=mock_saml_client,
            )

        assert exc_info.value.status_code == 400
        assert exc_info.value.detail == "Authentication failed"


class TestSAMLJackingPrevention:
    """Specific tests for SAML Jacking attack prevention"""

    @pytest.mark.anyio
    async def test_saml_jacking_with_crafted_response(self):
        """Test prevention of SAML jacking with completely crafted response"""
        # This is the exact attack that was demonstrated in the PoC
        crafted_response = base64.b64encode(
            b"""<samlp:Response xmlns:samlp="urn:oasis:names:tc:SAML:2.0:protocol"
                xmlns:saml="urn:oasis:names:tc:SAML:2.0:assertion"
                ID="_8e8dc5f69a98cc4c1ff3427e5ce34606fd672f91e6"
                Version="2.0"
                IssueInstant="2024-01-01T00:00:00Z"
                InResponseTo="_1234567890">
                <saml:Issuer>https://idp.example.com</saml:Issuer>
                <samlp:Status>
                    <samlp:StatusCode Value="urn:oasis:names:tc:SAML:2.0:status:Success"/>
                </samlp:Status>
                <saml:Assertion ID="_d71a3a8e9fcc45c9e9d248ef7049393fc8f04e5f75"
                    Version="2.0"
                    IssueInstant="2024-01-01T00:00:00Z">
                    <saml:Issuer>https://idp.example.com</saml:Issuer>
                    <saml:Subject>
                        <saml:NameID Format="urn:oasis:names:tc:SAML:1.1:nameid-format:emailAddress">
                            chris@tracecat.com
                        </saml:NameID>
                        <saml:SubjectConfirmation Method="urn:oasis:names:tc:SAML:2.0:cm:bearer">
                            <saml:SubjectConfirmationData NotOnOrAfter="2024-01-01T00:05:00Z"
                                Recipient="https://app.tracecat-staging.com/auth/saml/acs"
                                InResponseTo="_1234567890"/>
                        </saml:SubjectConfirmation>
                    </saml:Subject>
                    <saml:Conditions NotBefore="2024-01-01T00:00:00Z"
                        NotOnOrAfter="2024-01-01T00:05:00Z">
                        <saml:AudienceRestriction>
                            <saml:Audience>https://app.tracecat-staging.com</saml:Audience>
                        </saml:AudienceRestriction>
                    </saml:Conditions>
                    <saml:AuthnStatement AuthnInstant="2024-01-01T00:00:00Z">
                        <saml:AuthnContext>
                            <saml:AuthnContextClassRef>
                                urn:oasis:names:tc:SAML:2.0:ac:classes:Password
                            </saml:AuthnContextClassRef>
                        </saml:AuthnContext>
                    </saml:AuthnStatement>
                    <saml:AttributeStatement>
                        <saml:Attribute Name="email">
                            <saml:AttributeValue>chris@tracecat.com</saml:AttributeValue>
                        </saml:Attribute>
                    </saml:AttributeStatement>
                </saml:Assertion>
            </samlp:Response>"""
        ).decode()

        mock_request = MagicMock()
        mock_user_manager = AsyncMock()
        mock_strategy = MagicMock()
        mock_client = MagicMock()

        # This should fail immediately at the signature check
        with pytest.raises(HTTPException) as exc_info:
            await sso_acs(
                request=mock_request,
                saml_response=crafted_response,
                relay_state="fake_relay_state",
                user_manager=mock_user_manager,
                strategy=mock_strategy,
                client=mock_client,
            )

        assert exc_info.value.status_code == 400
        assert exc_info.value.detail == "Authentication failed"
        # Should fail because there's no signature element

    @pytest.mark.anyio
    async def test_saml_jacking_with_fake_signature(self):
        """Test prevention of SAML jacking with fake signature element"""
        # Attack with a fake signature element
        crafted_response = base64.b64encode(
            b"""<samlp:Response xmlns:samlp="urn:oasis:names:tc:SAML:2.0:protocol"
                xmlns:saml="urn:oasis:names:tc:SAML:2.0:assertion"
                ID="_8e8dc5f69a98cc4c1ff3427e5ce34606fd672f91e6"
                Version="2.0"
                IssueInstant="2024-01-01T00:00:00Z"
                InResponseTo="_1234567890">
                <saml:Issuer>https://idp.example.com</saml:Issuer>
                <ds:Signature xmlns:ds="http://www.w3.org/2000/09/xmldsig#">
                    <ds:SignatureValue>FAKE_SIGNATURE_VALUE</ds:SignatureValue>
                </ds:Signature>
                <samlp:Status>
                    <samlp:StatusCode Value="urn:oasis:names:tc:SAML:2.0:status:Success"/>
                </samlp:Status>
                <saml:Assertion ID="_d71a3a8e9fcc45c9e9d248ef7049393fc8f04e5f75">
                    <saml:AttributeStatement>
                        <saml:Attribute Name="email">
                            <saml:AttributeValue>chris@tracecat.com</saml:AttributeValue>
                        </saml:Attribute>
                    </saml:AttributeStatement>
                </saml:Assertion>
            </samlp:Response>"""
        ).decode()

        mock_request = MagicMock()
        mock_user_manager = AsyncMock()
        mock_strategy = MagicMock()
        mock_client = MagicMock()

        # Even with a signature element, it should fail when pysaml2 validates
        mock_client.parse_authn_request_response.side_effect = Exception(
            "Signature verification failed"
        )

        with pytest.raises(HTTPException) as exc_info:
            await sso_acs(
                request=mock_request,
                saml_response=crafted_response,
                relay_state="fake_relay_state",
                user_manager=mock_user_manager,
                strategy=mock_strategy,
                client=mock_client,
            )

        assert exc_info.value.status_code == 400
        assert exc_info.value.detail == "Authentication failed"
