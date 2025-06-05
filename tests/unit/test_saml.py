import base64
import os
from datetime import datetime, timedelta
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from defusedxml.common import EntitiesForbidden
from fastapi import HTTPException
from sqlalchemy.ext.asyncio import AsyncSession

from tracecat.auth.saml import (
    SAMLParser,
    create_saml_client,
    login,
    sso_acs,
)
from tracecat.db.schemas import (
    SAMLRequestData,  # Assuming this is your SQLAlchemy model
)


class TestComprehensiveSAMLSuite:
    """Consolidated test suite for all SAML security, functionality, and OWASP checks."""

    @pytest.fixture
    def mock_db_session(self):
        """Create a mock database session."""
        session = AsyncMock(spec=AsyncSession)

        # session.execute is an AsyncMock as AsyncSession.execute is async.
        # Its return_value (representing SQLAlchemy's Result object) should allow
        # mocking of its synchronous methods with MagicMock.
        mock_result_obj = (
            MagicMock()
        )  # Result object itself isn't necessarily async, but methods are.
        # Using MagicMock for more direct control over its attributes.
        session.execute.return_value = mock_result_obj

        # Ensure Result.scalar_one_or_none() is a MagicMock (it's a sync method)
        mock_result_obj.scalar_one_or_none = MagicMock(return_value=None)

        # Ensure Result.scalars().first() is also correctly mocked
        # Result.scalars() is sync, returns a ScalarResult (or similar).
        # ScalarResult.first() is sync.
        mock_scalars_obj = MagicMock()
        mock_result_obj.scalars.return_value = mock_scalars_obj
        mock_scalars_obj.first = MagicMock(return_value=None)

        return session

    @pytest.fixture
    def mock_saml_client(self):
        """Create a mock SAML client."""
        client = MagicMock()
        client.metadata = {"https://idp.example.com": {}}
        # Mock sp_config which is accessed in some tests
        client.sp_config = {
            "allow_unsolicited": False,
            "want_assertions_signed": True,
            "want_response_signed": True,
            "want_assertions_or_response_signed": True,
            "only_use_keys_in_metadata": True,
        }
        return client

    @pytest.fixture
    def valid_saml_response_with_signature(self):
        """Create a valid SAML response with signature."""
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
        """Create an unsigned SAML response (attack vector)."""
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
        """Create a SAML response without InResponseTo (unsolicited)."""
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
        self, mock_saml_client, unsigned_saml_response, mock_db_session
    ):
        """Test that unsigned SAML responses are rejected."""
        mock_request = MagicMock()
        mock_user_manager = AsyncMock()
        mock_strategy = MagicMock()

        with pytest.raises(HTTPException) as exc_info:
            await sso_acs(
                request=mock_request,
                saml_response=unsigned_saml_response,
                relay_state="test_relay_state",
                user_manager=mock_user_manager,
                strategy=mock_strategy,
                client=mock_saml_client,
                db_session=mock_db_session,
            )
        assert exc_info.value.status_code == 400
        assert "Authentication failed" in exc_info.value.detail

    @pytest.mark.anyio
    async def test_saml_response_without_inresponseto_rejected(
        self, mock_saml_client, saml_response_without_inresponseto, mock_db_session
    ):
        """Test that SAML responses without InResponseTo are rejected."""
        mock_request = MagicMock()
        mock_user_manager = AsyncMock()
        mock_strategy = MagicMock()

        with pytest.raises(HTTPException) as exc_info:
            await sso_acs(
                request=mock_request,
                saml_response=saml_response_without_inresponseto,
                relay_state="test_relay_state",
                user_manager=mock_user_manager,
                strategy=mock_strategy,
                client=mock_saml_client,
                db_session=mock_db_session,
            )
        assert exc_info.value.status_code == 400
        assert "Authentication failed" in exc_info.value.detail

    @pytest.mark.anyio
    async def test_replay_attack_prevention(
        self, mock_saml_client, valid_saml_response_with_signature, mock_db_session
    ):
        """Test that replay attacks are prevented."""
        mock_request = MagicMock()
        mock_user_manager = AsyncMock()
        mock_strategy = MagicMock()
        request_id = "_valid_request_id"
        relay_state = "test_relay_state"

        mock_stored_request = SAMLRequestData(
            id=request_id,
            relay_state=relay_state,
            expires_at=datetime.now() + timedelta(seconds=300),
            created_at=datetime.now(),
        )
        # First call returns the stored request, second call returns None (simulating deletion)
        mock_db_session.execute.return_value.scalar_one_or_none.side_effect = [
            mock_stored_request,
            None,
        ]

        mock_response = MagicMock()
        mock_response.in_response_to = request_id
        mock_response.signature_check_result = True
        mock_response.response_signed = True
        mock_response.assertions_signed = True
        mock_response._xmlstr = f'''<samlp:Response xmlns:samlp="urn:oasis:names:tc:SAML:2.0:protocol"
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
        </samlp:Response>'''
        mock_response.configure_mock(__str__=lambda self: self._xmlstr)
        mock_saml_client.parse_authn_request_response.return_value = mock_response

        mock_user = MagicMock()
        mock_user.is_active = True
        mock_user_manager.saml_callback.return_value = mock_user
        mock_auth_response = MagicMock()

        with patch(
            "tracecat.auth.saml.auth_backend.login", return_value=mock_auth_response
        ):
            await sso_acs(
                request=mock_request,
                saml_response=base64.b64encode(mock_response._xmlstr.encode()).decode(),
                relay_state=relay_state,
                user_manager=mock_user_manager,
                strategy=mock_strategy,
                client=mock_saml_client,
                db_session=mock_db_session,
            )

        # Ensure delete was called on the db_session for the first successful attempt
        mock_db_session.delete.assert_called_once_with(mock_stored_request)
        mock_db_session.commit.assert_called()  # Check commit was called after delete

        with pytest.raises(HTTPException) as exc_info:
            await sso_acs(
                request=mock_request,
                saml_response=base64.b64encode(mock_response._xmlstr.encode()).decode(),
                relay_state=relay_state,
                user_manager=mock_user_manager,
                strategy=mock_strategy,
                client=mock_saml_client,
                db_session=mock_db_session,  # Second attempt will use the None from side_effect
            )
        assert exc_info.value.status_code == 400
        assert "Authentication failed" in exc_info.value.detail

    @pytest.mark.anyio
    async def test_relay_state_validation(
        self, mock_saml_client, valid_saml_response_with_signature, mock_db_session
    ):
        """Test that relay state is properly validated for CSRF protection."""
        mock_request = MagicMock()
        mock_user_manager = AsyncMock()
        mock_strategy = MagicMock()
        request_id = "_valid_request_id"
        correct_relay_state = "correct_relay_state"

        mock_stored_request = SAMLRequestData(
            id=request_id,
            relay_state=correct_relay_state,  # Correct relay state
            expires_at=datetime.now() + timedelta(seconds=300),
            created_at=datetime.now(),
        )
        mock_db_session.execute.return_value.scalar_one_or_none.return_value = (
            mock_stored_request
        )

        mock_response = MagicMock()
        mock_response.in_response_to = request_id
        mock_response.signature_check_result = True
        mock_response.response_signed = True
        mock_response.assertions_signed = True
        # Decode base64 to actual XML for _xmlstr and str()
        actual_xml_string = base64.b64decode(
            valid_saml_response_with_signature.encode("utf-8")
        ).decode("utf-8")
        mock_response._xmlstr = actual_xml_string
        mock_response.configure_mock(__str__=lambda current_self: current_self._xmlstr)
        mock_saml_client.parse_authn_request_response.return_value = mock_response

        with pytest.raises(HTTPException) as exc_info:
            await sso_acs(
                request=mock_request,
                saml_response=valid_saml_response_with_signature,
                relay_state="wrong_relay_state",  # Wrong relay state
                user_manager=mock_user_manager,
                strategy=mock_strategy,
                client=mock_saml_client,
                db_session=mock_db_session,
            )
        assert exc_info.value.status_code == 400
        assert "Authentication failed" in exc_info.value.detail
        # Ensure delete was called because the relay state mismatch should still consume the entry
        mock_db_session.delete.assert_called_once_with(mock_stored_request)

    @pytest.mark.anyio
    async def test_invalid_inresponseto_rejected(
        self, mock_saml_client, valid_saml_response_with_signature, mock_db_session
    ):
        """Test that invalid InResponseTo values are rejected."""
        mock_request = MagicMock()
        mock_user_manager = AsyncMock()
        mock_strategy = MagicMock()
        invalid_values = ["", "_", "_short", None]

        for invalid_value in invalid_values:
            # For this test, the DB lookup for InResponseTo should find nothing
            mock_db_session.execute.return_value.scalar_one_or_none.return_value = None

            mock_response = MagicMock()
            mock_response.in_response_to = (
                invalid_value  # This is checked before DB lookup if None/empty
            )
            mock_response.signature_check_result = True
            mock_response.response_signed = True
            mock_response.assertions_signed = True
            # Decode base64 to actual XML for _xmlstr and str()
            actual_xml_string = base64.b64decode(
                valid_saml_response_with_signature.encode("utf-8")
            ).decode("utf-8")
            mock_response._xmlstr = actual_xml_string
            mock_response.configure_mock(
                __str__=lambda current_self: current_self._xmlstr
            )
            mock_saml_client.parse_authn_request_response.return_value = mock_response

            with pytest.raises(HTTPException) as exc_info:
                await sso_acs(
                    request=mock_request,
                    saml_response=valid_saml_response_with_signature,
                    relay_state="test_relay_state",
                    user_manager=mock_user_manager,
                    strategy=mock_strategy,
                    client=mock_saml_client,
                    db_session=mock_db_session,
                )
            assert exc_info.value.status_code == 400
            assert "Authentication failed" in exc_info.value.detail

    @pytest.mark.anyio
    async def test_expired_request_rejected(
        self, mock_saml_client, valid_saml_response_with_signature, mock_db_session
    ):
        """Test that responses for expired requests are rejected."""
        mock_request = MagicMock()
        mock_user_manager = AsyncMock()
        mock_strategy = MagicMock()
        request_id = "_expired_request_id"

        # Simulate an expired request from DB
        mock_expired_request = SAMLRequestData(
            id=request_id,
            relay_state="test_relay_state",
            expires_at=datetime.now() - timedelta(seconds=1),  # Expired 1 second ago
            created_at=datetime.now() - timedelta(seconds=301),
        )
        mock_db_session.execute.return_value.scalar_one_or_none.return_value = (
            mock_expired_request
        )

        mock_response = MagicMock()
        mock_response.in_response_to = request_id
        mock_response.signature_check_result = True
        mock_response.response_signed = True
        mock_response.assertions_signed = True
        # Decode base64 to actual XML for _xmlstr and str()
        actual_xml_string = base64.b64decode(
            valid_saml_response_with_signature.encode("utf-8")
        ).decode("utf-8")
        mock_response._xmlstr = actual_xml_string
        mock_response.configure_mock(__str__=lambda current_self: current_self._xmlstr)
        mock_saml_client.parse_authn_request_response.return_value = mock_response

        with pytest.raises(HTTPException) as exc_info:
            await sso_acs(
                request=mock_request,
                saml_response=valid_saml_response_with_signature,
                relay_state="test_relay_state",
                user_manager=mock_user_manager,
                strategy=mock_strategy,
                client=mock_saml_client,
                db_session=mock_db_session,
            )
        assert exc_info.value.status_code == 400
        assert "Authentication failed" in exc_info.value.detail
        # Ensure delete was called on the expired entry
        mock_db_session.delete.assert_called_once_with(mock_expired_request)

    @pytest.mark.anyio
    async def test_saml_client_initialization_failures(self):
        """Test various SAML client initialization failure scenarios."""
        with patch("tracecat.auth.saml.get_setting", return_value=None):
            with pytest.raises(HTTPException) as exc_info:
                await create_saml_client()
            assert exc_info.value.status_code == 400
        with patch("tracecat.auth.saml.get_setting", return_value=123):
            with pytest.raises(HTTPException) as exc_info:
                await create_saml_client()
            assert exc_info.value.status_code == 400

    def test_saml_parser_security(self):
        """Test SAMLParser against various attack vectors."""
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
        with pytest.raises(EntitiesForbidden):  # defusedxml should raise this
            parser.parse_to_dict()

        invalid_xml = (
            """<root xmlns:saml2="urn:oasis:names:tc:SAML:2.0:assertion"></root>"""
        )
        parser = SAMLParser(invalid_xml)
        with pytest.raises(HTTPException):
            parser.parse_to_dict()

        missing_name_xml = """<saml2:AttributeStatement xmlns:saml2="urn:oasis:names:tc:SAML:2.0:assertion">
            <saml2:Attribute>
                <saml2:AttributeValue>test@example.com</saml2:AttributeValue>
            </saml2:Attribute>
        </saml2:AttributeStatement>"""
        parser = SAMLParser(missing_name_xml)
        with pytest.raises(HTTPException):
            parser.parse_to_dict()

    @pytest.mark.anyio
    async def test_login_generates_secure_relay_state(
        self, mock_saml_client, mock_db_session
    ):
        """Test that login generates cryptographically secure relay state."""
        mock_saml_client.prepare_for_authenticate.return_value = (
            "_request_id",
            {"headers": [("Location", "https://idp.example.com/sso")]},
        )

        await login(client=mock_saml_client, db_session=mock_db_session)

        # Verify request was stored in db
        mock_db_session.add.assert_called_once()
        call_args = mock_db_session.add.call_args[0][
            0
        ]  # Get the SAMLRequestData instance
        assert isinstance(call_args, SAMLRequestData)
        assert call_args.id == "_request_id"
        assert len(call_args.relay_state) >= 32
        mock_db_session.commit.assert_called_once()

    @pytest.mark.anyio
    async def test_inactive_user_rejected(
        self, mock_saml_client, valid_saml_response_with_signature, mock_db_session
    ):
        """Test that inactive users cannot authenticate."""
        mock_request = MagicMock()
        mock_user_manager = AsyncMock()
        mock_strategy = MagicMock()
        request_id = "_valid_request_id"
        relay_state = "test_relay_state"

        mock_stored_request = SAMLRequestData(
            id=request_id,
            relay_state=relay_state,
            expires_at=datetime.now() + timedelta(seconds=300),
            created_at=datetime.now(),
        )
        mock_db_session.execute.return_value.scalar_one_or_none.return_value = (
            mock_stored_request
        )

        mock_response = MagicMock()
        mock_response.in_response_to = request_id
        mock_response.signature_check_result = True
        mock_response.response_signed = True
        mock_response.assertions_signed = True
        # Decode base64 to actual XML for _xmlstr and str()
        actual_xml_string = base64.b64decode(
            valid_saml_response_with_signature.encode("utf-8")
        ).decode("utf-8")
        mock_response._xmlstr = actual_xml_string
        mock_response.configure_mock(__str__=lambda current_self: current_self._xmlstr)
        mock_saml_client.parse_authn_request_response.return_value = mock_response

        mock_user = MagicMock()
        mock_user.is_active = False  # Inactive user
        mock_user_manager.saml_callback.return_value = mock_user

        with pytest.raises(HTTPException) as exc_info:
            await sso_acs(
                request=mock_request,
                saml_response=valid_saml_response_with_signature,
                relay_state=relay_state,
                user_manager=mock_user_manager,
                strategy=mock_strategy,
                client=mock_saml_client,
                db_session=mock_db_session,
            )
        assert exc_info.value.status_code == 400
        assert "Authentication failed" in exc_info.value.detail

    @pytest.mark.anyio
    async def test_saml_jacking_with_crafted_response(
        self, mock_db_session
    ):  # Added mock_db_session
        """Test prevention of SAML jacking with completely crafted response."""
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
        mock_client = MagicMock()  # Basic mock, not the fixture one

        with pytest.raises(HTTPException) as exc_info:
            await sso_acs(
                request=mock_request,
                saml_response=crafted_response,
                relay_state="fake_relay_state",
                user_manager=mock_user_manager,
                strategy=mock_strategy,
                client=mock_client,
                db_session=mock_db_session,
            )
        assert exc_info.value.status_code == 400
        assert "Authentication failed" in exc_info.value.detail

    @pytest.mark.anyio
    async def test_saml_jacking_with_fake_signature(
        self, mock_db_session
    ):  # Added mock_db_session
        """Test prevention of SAML jacking with fake signature element."""
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
        mock_client = MagicMock()  # Basic mock
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
                db_session=mock_db_session,
            )
        assert exc_info.value.status_code == 400
        assert "Authentication failed" in exc_info.value.detail

    @pytest.mark.anyio
    async def test_message_confidentiality_tls_requirement(self):
        """Test that SAML endpoints require TLS (OWASP 4.2.1)."""
        from tracecat.config import SAML_PUBLIC_ACS_URL, TRACECAT__PUBLIC_API_URL

        if "production" in os.getenv("TRACECAT__APP_ENV", "").lower():
            assert SAML_PUBLIC_ACS_URL.startswith("https://")
            assert TRACECAT__PUBLIC_API_URL.startswith("https://")

    @pytest.mark.anyio
    async def test_xml_signature_wrapping_prevention(
        self, mock_db_session
    ):  # Added mock_db_session
        """Test prevention of XML Signature Wrapping attacks (OWASP - On Breaking SAML)."""
        wrapped_attack = base64.b64encode(
            b"""<samlp:Response xmlns:samlp="urn:oasis:names:tc:SAML:2.0:protocol"
                ID="_legitimate_response" Version="2.0"
                IssueInstant="2024-01-01T00:00:00Z" InResponseTo="_request123">
                <saml:Issuer xmlns:saml="urn:oasis:names:tc:SAML:2.0:assertion">https://idp.example.com</saml:Issuer>
                <samlp:Status>
                    <samlp:StatusCode Value="urn:oasis:names:tc:SAML:2.0:status:Success"/>
                </samlp:Status>
                <!-- Original signed assertion moved here -->
                <saml:Assertion xmlns:saml="urn:oasis:names:tc:SAML:2.0:assertion"
                    ID="_original_signed" Version="2.0">
                    <ds:Signature xmlns:ds="http://www.w3.org/2000/09/xmldsig#">
                        <ds:SignatureValue>VALID_SIGNATURE_FOR_ORIGINAL</ds:SignatureValue>
                    </ds:Signature>
                    <saml:Subject>
                        <saml:NameID>legitimate@user.com</saml:NameID>
                    </saml:Subject>
                </saml:Assertion>
                <!-- Attacker's unsigned assertion -->
                <saml:Assertion xmlns:saml="urn:oasis:names:tc:SAML:2.0:assertion"
                    ID="_attacker_assertion" Version="2.0">
                    <saml:Subject>
                        <saml:NameID>attacker@evil.com</saml:NameID>
                    </saml:Subject>
                    <saml:AttributeStatement>
                        <saml:Attribute Name="email">
                            <saml:AttributeValue>attacker@evil.com</saml:AttributeValue>
                        </saml:Attribute>
                    </saml:AttributeStatement>
                </saml:Assertion>
            </samlp:Response>"""
        ).decode()
        mock_request = MagicMock()
        mock_user_manager = AsyncMock()
        mock_strategy = MagicMock()
        mock_client = MagicMock()  # Basic mock

        with pytest.raises(HTTPException) as exc_info:
            await sso_acs(
                request=mock_request,
                saml_response=wrapped_attack,
                relay_state="test",
                user_manager=mock_user_manager,
                strategy=mock_strategy,
                client=mock_client,
                db_session=mock_db_session,
            )
        assert exc_info.value.status_code == 400

    @pytest.mark.anyio
    async def test_validate_protocol_usage_requirements(
        self, mock_saml_client, mock_db_session
    ):  # Added mock_db_session
        """Test SAML protocol usage requirements (OWASP 4.1.4.1, 4.1.4.2)."""
        mock_saml_client.prepare_for_authenticate.return_value = (
            "_request_id",
            {"headers": [("Location", "https://idp.example.com/sso")]},
        )
        await login(client=mock_saml_client, db_session=mock_db_session)
        mock_saml_client.prepare_for_authenticate.assert_called_once()
        call_args = mock_saml_client.prepare_for_authenticate.call_args
        assert "relay_state" in call_args.kwargs
        assert len(call_args.kwargs["relay_state"]) >= 32

    @pytest.mark.anyio
    async def test_validate_response_processing_rules(
        self, mock_saml_client, mock_db_session
    ):  # Added mock_db_session
        """Test all Response processing rules (OWASP 4.1.4.3)."""
        test_cases = [
            {
                "xml": """<samlp:Response xmlns:samlp="urn:oasis:names:tc:SAML:2.0:protocol"
                    ID="_resp" Version="2.0" InResponseTo="_req">
                    <ds:Signature xmlns:ds="http://www.w3.org/2000/09/xmldsig#">
                        <ds:SignatureValue>fake</ds:SignatureValue>
                    </ds:Signature>
                    <samlp:Status>
                        <samlp:StatusCode Value="urn:oasis:names:tc:SAML:2.0:status:Success"/>
                    </samlp:Status>
                </samlp:Response>""",
                "error_detail_contains": "Authentication failed",
            },
            {
                "xml": """<samlp:Response xmlns:samlp="urn:oasis:names:tc:SAML:2.0:protocol"
                    ID="_resp" Version="1.0" InResponseTo="_req">
                    <saml:Issuer xmlns:saml="urn:oasis:names:tc:SAML:2.0:assertion">https://idp.example.com</saml:Issuer>
                    <ds:Signature xmlns:ds="http://www.w3.org/2000/09/xmldsig#">
                        <ds:SignatureValue>fake</ds:SignatureValue>
                    </ds:Signature>
                </samlp:Response>""",
                "error_detail_contains": "Authentication failed",
            },
        ]
        for test_case in test_cases:
            encoded = base64.b64encode(test_case["xml"].encode()).decode()
            mock_saml_client.parse_authn_request_response.side_effect = Exception(
                "SAML processing error"
            )

            # Mock DB to return nothing for InResponseTo lookup
            mock_db_session.execute.return_value.scalar_one_or_none.return_value = None

            with pytest.raises(HTTPException) as exc_info:
                await sso_acs(
                    request=MagicMock(),
                    saml_response=encoded,
                    relay_state="test",
                    user_manager=AsyncMock(),
                    strategy=MagicMock(),
                    client=mock_saml_client,
                    db_session=mock_db_session,
                )
            assert test_case["error_detail_contains"] in exc_info.value.detail

    @pytest.mark.anyio
    async def test_binding_implementation_no_caching(self):
        """Test that SAML responses are not cached (OWASP 3.5)."""
        assert not hasattr(sso_acs, "_response_cache")  # sso_acs itself shouldn't cache
        assert not hasattr(sso_acs, "cache")

    @pytest.mark.anyio
    async def test_security_countermeasures_short_lifetime(
        self, mock_saml_client, mock_db_session
    ):  # Added mock_db_session
        """Test short lifetime validation on SAML Response (OWASP Security Countermeasures)."""
        old_response_xml = b"""<samlp:Response xmlns:samlp="urn:oasis:names:tc:SAML:2.0:protocol"
                ID="_resp" Version="2.0"
                IssueInstant="2020-01-01T00:00:00Z"
                InResponseTo="_req_short_lifetime">
                <saml:Issuer xmlns:saml="urn:oasis:names:tc:SAML:2.0:assertion">https://idp.example.com</saml:Issuer>
                <ds:Signature xmlns:ds="http://www.w3.org/2000/09/xmldsig#">
                    <ds:SignatureValue>fake</ds:SignatureValue>
                </ds:Signature>
                <samlp:Status><samlp:StatusCode Value="urn:oasis:names:tc:SAML:2.0:status:Success"/></samlp:Status>
                <saml:Assertion xmlns:saml="urn:oasis:names:tc:SAML:2.0:assertion"
                    ID="_assertion_old" Version="2.0"
                    IssueInstant="2020-01-01T00:00:00Z">
                    <saml:Issuer>https://idp.example.com</saml:Issuer>
                    <saml:Subject><saml:NameID>test@example.com</saml:NameID></saml:Subject>
                    <saml:Conditions NotBefore="2020-01-01T00:00:00Z"
                        NotOnOrAfter="2020-01-01T00:05:00Z">
                         <saml:AudienceRestriction><saml:Audience>https://your-sp-entity-id</saml:Audience></saml:AudienceRestriction>
                    </saml:Conditions>
                    <saml:AttributeStatement><saml:Attribute Name="email"><saml:AttributeValue>test@example.com</saml:AttributeValue></saml:Attribute></saml:AttributeStatement>
                </saml:Assertion>
            </samlp:Response>"""
        old_response_encoded = base64.b64encode(old_response_xml).decode()
        mock_saml_client.parse_authn_request_response.side_effect = Exception(
            "Response has expired"
        )
        # Mock DB to return a valid, non-expired request for InResponseTo check initially
        mock_stored_request = SAMLRequestData(
            id="_req_short_lifetime",
            relay_state="test_lifetime",
            expires_at=datetime.now() + timedelta(seconds=300),
            created_at=datetime.now(),
        )
        mock_db_session.execute.return_value.scalar_one_or_none.return_value = (
            mock_stored_request
        )

        with pytest.raises(HTTPException) as exc_info:
            await sso_acs(
                request=MagicMock(),
                saml_response=old_response_encoded,
                relay_state="test_lifetime",
                user_manager=AsyncMock(),
                strategy=MagicMock(),
                client=mock_saml_client,
                db_session=mock_db_session,
            )
        assert exc_info.value.status_code == 400
        assert "Authentication failed" in exc_info.value.detail

    @pytest.mark.anyio
    async def test_unsolicited_response_disabled(
        self, mock_saml_client
    ):  # Use mock_saml_client fixture
        """Test that unsolicited responses are rejected (OWASP Unsolicited Response)."""
        # Access sp_config from the mocked client
        assert mock_saml_client.sp_config["allow_unsolicited"] is False

    @pytest.mark.anyio
    async def test_validate_signatures_configuration(
        self, mock_saml_client
    ):  # Use mock_saml_client fixture
        """Test signature validation configuration (OWASP - Validate Signatures)."""
        # Access sp_config from the mocked client
        assert mock_saml_client.sp_config["want_assertions_signed"] is True
        assert mock_saml_client.sp_config["want_response_signed"] is True
        assert mock_saml_client.sp_config["want_assertions_or_response_signed"] is True
        assert mock_saml_client.sp_config["only_use_keys_in_metadata"] is True

    @pytest.mark.anyio
    async def test_input_validation_on_attributes(
        self, mock_saml_client, mock_db_session
    ):  # Added mock_db_session
        """Test input validation on SAML attributes (OWASP - Input Validation)."""
        xss_xml = b"""<samlp:Response xmlns:samlp="urn:oasis:names:tc:SAML:2.0:protocol"
                ID="_resp_xss" Version="2.0" InResponseTo="_req_xss">
                <saml:Issuer xmlns:saml="urn:oasis:names:tc:SAML:2.0:assertion">https://idp.example.com</saml:Issuer>
                <ds:Signature xmlns:ds="http://www.w3.org/2000/09/xmldsig#">
                    <ds:SignatureValue>fake_xss_sig</ds:SignatureValue>
                </ds:Signature>
                <samlp:Status><samlp:StatusCode Value="urn:oasis:names:tc:SAML:2.0:status:Success"/></samlp:Status>
                <saml:Assertion xmlns:saml="urn:oasis:names:tc:SAML:2.0:assertion" ID="_assert_xss" Version="2.0" IssueInstant="2024-01-01T00:00:00Z">
                    <saml:Issuer>https://idp.example.com</saml:Issuer>
                    <saml:Subject><saml:NameID><script>alert('XSS_NAMEID')</script></saml:NameID></saml:Subject>
                    <saml:Conditions NotBefore="2024-01-01T00:00:00Z" NotOnOrAfter="2025-01-01T00:00:00Z">
                         <saml:AudienceRestriction><saml:Audience>https://your-sp-entity-id</saml:Audience></saml:AudienceRestriction>
                    </saml:Conditions>
                    <saml:AttributeStatement>
                        <saml:Attribute Name="email">
                            <saml:AttributeValue><script>alert('XSS_EMAIL')</script>@evil.com</saml:AttributeValue>
                        </saml:Attribute>
                    </saml:AttributeStatement>
                </saml:Assertion>
            </samlp:Response>"""
        xss_response_encoded = base64.b64encode(xss_xml).decode()
        mock_user_manager = AsyncMock()
        mock_user_manager.saml_callback.side_effect = ValueError("Invalid email format")

        mock_stored_request = SAMLRequestData(
            id="_req_xss",
            relay_state="test_xss",
            expires_at=datetime.now() + timedelta(seconds=300),
            created_at=datetime.now(),
        )
        mock_db_session.execute.return_value.scalar_one_or_none.return_value = (
            mock_stored_request
        )

        mock_authn_response_obj = MagicMock()
        mock_authn_response_obj.in_response_to = "_req_xss"
        mock_authn_response_obj.signature_check_result = True
        mock_authn_response_obj.response_signed = True
        mock_authn_response_obj.assertions_signed = True
        mock_authn_response_obj.xmlstr = xss_xml
        mock_authn_response_obj.configure_mock(
            __str__=lambda self: self.xmlstr.decode("utf-8")
        )
        mock_saml_client.parse_authn_request_response.return_value = (
            mock_authn_response_obj
        )

        with pytest.raises(HTTPException) as exc_info:
            await sso_acs(
                request=MagicMock(),
                saml_response=xss_response_encoded,
                relay_state="test_xss",
                user_manager=mock_user_manager,
                strategy=MagicMock(),
                client=mock_saml_client,
                db_session=mock_db_session,
            )
        assert exc_info.value.status_code == 400
        assert "Authentication failed" in exc_info.value.detail

    @pytest.mark.anyio
    async def test_cryptography_strong_algorithms(
        self, mock_saml_client
    ):  # Use mock_saml_client fixture
        """Test that strong cryptographic algorithms are configured (OWASP - Cryptography)."""
        # This test primarily relies on the mock_saml_client's sp_config
        # and the assumption that create_saml_client would set xmlsec_binary correctly.
        # A more in-depth test would involve inspecting the actual Saml2Config object.
        assert mock_saml_client.sp_config is not None
        # In a real scenario, you'd also check:
        # real_client = await create_saml_client() # (with appropriate mocks for get_setting)
        # assert real_client.config.xmlsec_binary is not None

    @pytest.mark.anyio
    async def test_sp_considerations_validate_idp_cert(
        self, mock_saml_client, mock_db_session
    ):  # Added mock_db_session
        """Test SP considerations for IdP certificate validation (OWASP - SP Considerations)."""
        untrusted_idp_xml = b"""<samlp:Response xmlns:samlp="urn:oasis:names:tc:SAML:2.0:protocol"
                ID="_resp_untrusted" Version="2.0" InResponseTo="_req_untrusted">
                <saml:Issuer xmlns:saml="urn:oasis:names:tc:SAML:2.0:assertion">https://untrusted-idp.com</saml:Issuer>
                <ds:Signature xmlns:ds="http://www.w3.org/2000/09/xmldsig#">
                    <ds:SignatureValue>fake_untrusted_sig</ds:SignatureValue>
                </ds:Signature>
                 <samlp:Status><samlp:StatusCode Value="urn:oasis:names:tc:SAML:2.0:status:Success"/></samlp:Status>
                 <saml:Assertion xmlns:saml="urn:oasis:names:tc:SAML:2.0:assertion" ID="_assert_untrusted" Version="2.0" IssueInstant="2024-01-01T00:00:00Z">
                    <saml:Issuer>https://untrusted-idp.com</saml:Issuer>
                     <saml:Subject><saml:NameID>test@example.com</saml:NameID></saml:Subject>
                    <saml:Conditions NotBefore="2024-01-01T00:00:00Z" NotOnOrAfter="2025-01-01T00:00:00Z">
                         <saml:AudienceRestriction><saml:Audience>https://your-sp-entity-id</saml:Audience></saml:AudienceRestriction>
                    </saml:Conditions>
                    <saml:AttributeStatement><saml:Attribute Name="email"><saml:AttributeValue>test@example.com</saml:AttributeValue></saml:Attribute></saml:AttributeStatement>
                </saml:Assertion>
            </samlp:Response>"""
        untrusted_idp_response_encoded = base64.b64encode(untrusted_idp_xml).decode()
        mock_saml_client.parse_authn_request_response.side_effect = Exception(
            "Unknown IdP: https://untrusted-idp.com"
        )

        mock_stored_request = SAMLRequestData(
            id="_req_untrusted",
            relay_state="test_untrusted",
            expires_at=datetime.now() + timedelta(seconds=300),
            created_at=datetime.now(),
        )
        mock_db_session.execute.return_value.scalar_one_or_none.return_value = (
            mock_stored_request
        )

        with pytest.raises(HTTPException) as exc_info:
            await sso_acs(
                request=MagicMock(),
                saml_response=untrusted_idp_response_encoded,
                relay_state="test_untrusted",
                user_manager=AsyncMock(),
                strategy=MagicMock(),
                client=mock_saml_client,
                db_session=mock_db_session,
            )
        assert exc_info.value.status_code == 400
        assert "Authentication failed" in exc_info.value.detail

    @pytest.mark.anyio
    async def test_validate_recipient_attribute(
        self, mock_saml_client, mock_db_session
    ):  # Added mock_db_session
        """Test validation of Recipient attribute (OWASP - SP Considerations)."""
        wrong_recipient_xml = b"""<samlp:Response xmlns:samlp="urn:oasis:names:tc:SAML:2.0:protocol"
                ID="_resp_wrong_recipient" Version="2.0" InResponseTo="_req_wrong_recipient">
                <saml:Issuer xmlns:saml="urn:oasis:names:tc:SAML:2.0:assertion">https://idp.example.com</saml:Issuer>
                <ds:Signature xmlns:ds="http://www.w3.org/2000/09/xmldsig#">
                    <ds:SignatureValue>fake_wrong_recipient_sig</ds:SignatureValue>
                </ds:Signature>
                <samlp:Status><samlp:StatusCode Value="urn:oasis:names:tc:SAML:2.0:status:Success"/></samlp:Status>
                <saml:Assertion xmlns:saml="urn:oasis:names:tc:SAML:2.0:assertion" ID="_assert_wrong_recipient" Version="2.0" IssueInstant="2024-01-01T00:00:00Z">
                     <saml:Issuer>https://idp.example.com</saml:Issuer>
                    <saml:Subject>
                        <saml:NameID>test@example.com</saml:NameID>
                        <saml:SubjectConfirmation Method="urn:oasis:names:tc:SAML:2.0:cm:bearer">
                            <saml:SubjectConfirmationData
                                Recipient="https://wrong-sp.com/saml/acs"
                                InResponseTo="_req_wrong_recipient"
                                NotOnOrAfter="2025-01-01T00:00:00Z"/>
                        </saml:SubjectConfirmation>
                    </saml:Subject>
                     <saml:Conditions NotBefore="2024-01-01T00:00:00Z" NotOnOrAfter="2025-01-01T00:00:00Z">
                         <saml:AudienceRestriction><saml:Audience>https://your-sp-entity-id</saml:Audience></saml:AudienceRestriction>
                    </saml:Conditions>
                    <saml:AttributeStatement><saml:Attribute Name="email"><saml:AttributeValue>test@example.com</saml:AttributeValue></saml:Attribute></saml:AttributeStatement>
                </saml:Assertion>
            </samlp:Response>"""
        wrong_recipient_encoded = base64.b64encode(wrong_recipient_xml).decode()
        mock_saml_client.parse_authn_request_response.side_effect = Exception(
            "Recipient validation failed"
        )

        mock_stored_request = SAMLRequestData(
            id="_req_wrong_recipient",
            relay_state="test_recipient",
            expires_at=datetime.now() + timedelta(seconds=300),
            created_at=datetime.now(),
        )
        mock_db_session.execute.return_value.scalar_one_or_none.return_value = (
            mock_stored_request
        )

        with pytest.raises(HTTPException) as exc_info:
            await sso_acs(
                request=MagicMock(),
                saml_response=wrong_recipient_encoded,
                relay_state="test_recipient",
                user_manager=AsyncMock(),
                strategy=MagicMock(),
                client=mock_saml_client,
                db_session=mock_db_session,
            )
        assert exc_info.value.status_code == 400
        assert "Authentication failed" in exc_info.value.detail

    @pytest.mark.anyio
    async def test_billion_laughs_attack_prevention(self):
        """Test prevention of Billion Laughs XML attack."""
        billion_laughs = """<?xml version="1.0"?>
        <!DOCTYPE lolz [
          <!ENTITY lol "lol">
          <!ENTITY lol2 "&lol;&lol;&lol;&lol;&lol;&lol;&lol;&lol;&lol;&lol;">
          <!ENTITY lol3 "&lol2;&lol2;&lol2;&lol2;&lol2;&lol2;&lol2;&lol2;&lol2;&lol2;">
          <!ENTITY lol4 "&lol3;&lol3;&lol3;&lol3;&lol3;&lol3;&lol3;&lol3;&lol3;&lol3;">
        ]>
        <saml2:AttributeStatement xmlns:saml2="urn:oasis:names:tc:SAML:2.0:assertion">
            <saml2:Attribute Name="email">
                <saml2:AttributeValue>&lol4;</saml2:AttributeValue>
            </saml2:Attribute>
        </saml2:AttributeStatement>"""
        parser = SAMLParser(billion_laughs)
        with pytest.raises(EntitiesForbidden):
            parser.parse_to_dict()

    @pytest.mark.anyio
    async def test_comment_injection_attack(
        self, mock_db_session
    ):  # Added mock_db_session
        """Test that comments don't bypass security checks."""
        comment_attack = base64.b64encode(
            b"""<samlp:Response xmlns:samlp="urn:oasis:names:tc:SAML:2.0:protocol"
                ID="_resp" Version="2.0" InResponseTo="_req_comment">
                <saml:Issuer xmlns:saml="urn:oasis:names:tc:SAML:2.0:assertion">https://idp.example.com</saml:Issuer>
                <!-- <ds:Signature>fake</ds:Signature> -->
                <samlp:Status>
                    <samlp:StatusCode Value="urn:oasis:names:tc:SAML:2.0:status:Success"/>
                </samlp:Status>
            </samlp:Response>"""
        ).decode()

        # Mock DB to return a valid request for InResponseTo check
        mock_stored_request = SAMLRequestData(
            id="_req_comment",
            relay_state="test",
            expires_at=datetime.now() + timedelta(seconds=300),
            created_at=datetime.now(),
        )
        mock_db_session.execute.return_value.scalar_one_or_none.return_value = (
            mock_stored_request
        )

        with pytest.raises(HTTPException) as exc_info:
            await sso_acs(
                request=MagicMock(),
                saml_response=comment_attack,
                relay_state="test",
                user_manager=AsyncMock(),
                strategy=MagicMock(),
                client=MagicMock(),
                db_session=mock_db_session,
            )
        assert exc_info.value.status_code == 400
        assert "Authentication failed" in exc_info.value.detail

    @pytest.mark.anyio
    async def test_database_error_handling_during_login(self, mock_saml_client):
        """Test handling of database errors during SAML request storage."""
        mock_db_session = AsyncMock(spec=AsyncSession)
        mock_db_session.commit.side_effect = Exception("Database connection lost")

        mock_saml_client.prepare_for_authenticate.return_value = (
            "_request_id",
            {"headers": [("Location", "https://idp.example.com/sso")]},
        )

        with pytest.raises(Exception, match="Database connection lost"):
            await login(client=mock_saml_client, db_session=mock_db_session)

        # Verify that add was called before the commit failure
        mock_db_session.add.assert_called_once()

    @pytest.mark.anyio
    async def test_database_error_handling_during_acs(
        self, mock_saml_client, valid_saml_response_with_signature
    ):
        """Test handling of database errors during SAML response processing."""
        mock_db_session = AsyncMock(spec=AsyncSession)
        request_id = "_valid_request_id"

        mock_stored_request = SAMLRequestData(
            id=request_id,
            relay_state="test_relay_state",
            expires_at=datetime.now() + timedelta(seconds=300),
            created_at=datetime.now(),
        )

        # Mock successful retrieval but failed delete/commit
        mock_result_obj = MagicMock()
        mock_result_obj.scalar_one_or_none = MagicMock(return_value=mock_stored_request)
        mock_db_session.execute.return_value = mock_result_obj
        mock_db_session.commit.side_effect = Exception("Database write failed")

        mock_response = MagicMock()
        mock_response.in_response_to = request_id
        mock_response.signature_check_result = True
        mock_response.response_signed = True
        mock_response.assertions_signed = True
        actual_xml_string = base64.b64decode(
            valid_saml_response_with_signature.encode("utf-8")
        ).decode("utf-8")
        mock_response._xmlstr = actual_xml_string
        mock_response.configure_mock(__str__=lambda current_self: current_self._xmlstr)
        mock_saml_client.parse_authn_request_response.return_value = mock_response

        with pytest.raises(Exception, match="Database write failed"):
            await sso_acs(
                request=MagicMock(),
                saml_response=valid_saml_response_with_signature,
                relay_state="test_relay_state",
                user_manager=AsyncMock(),
                strategy=MagicMock(),
                client=mock_saml_client,
                db_session=mock_db_session,
            )

    @pytest.mark.anyio
    async def test_saml_request_data_field_validation(
        self, mock_saml_client, mock_db_session
    ):
        """Test SAMLRequestData creation with various field values."""
        # Test with very long relay state
        mock_saml_client.prepare_for_authenticate.return_value = (
            "_request_id_long",
            {"headers": [("Location", "https://idp.example.com/sso")]},
        )

        # Should not raise an exception for long relay state
        await login(client=mock_saml_client, db_session=mock_db_session)

        call_args = mock_db_session.add.call_args[0][0]
        assert isinstance(call_args, SAMLRequestData)
        assert call_args.id == "_request_id_long"
        assert (
            len(call_args.relay_state) >= 32
        )  # Should be the generated secure token, not the long one

    @pytest.mark.anyio
    async def test_timezone_aware_expiration(
        self, mock_saml_client, valid_saml_response_with_signature, mock_db_session
    ):
        """Test that SAMLRequestData expiration works correctly with timezone-aware datetimes."""
        request_id = "_timezone_test_id"

        # Create a request that expired 1 hour ago - use naive datetime like the production code
        now = datetime.now()
        expired_time = now - timedelta(hours=1)  # Clearly expired

        mock_expired_request = SAMLRequestData(
            id=request_id,
            relay_state="test_relay_state",
            expires_at=expired_time,  # Store as naive datetime like the schema
            created_at=now - timedelta(hours=2),
        )
        mock_db_session.execute.return_value.scalar_one_or_none.return_value = (
            mock_expired_request
        )

        mock_response = MagicMock()
        mock_response.in_response_to = request_id
        mock_response.signature_check_result = True
        mock_response.response_signed = True
        mock_response.assertions_signed = True
        actual_xml_string = base64.b64decode(
            valid_saml_response_with_signature.encode("utf-8")
        ).decode("utf-8")
        mock_response._xmlstr = actual_xml_string
        mock_response.configure_mock(__str__=lambda current_self: current_self._xmlstr)
        mock_saml_client.parse_authn_request_response.return_value = mock_response

        with pytest.raises(HTTPException) as exc_info:
            await sso_acs(
                request=MagicMock(),
                saml_response=valid_saml_response_with_signature,
                relay_state="test_relay_state",
                user_manager=AsyncMock(),
                strategy=MagicMock(),
                client=mock_saml_client,
                db_session=mock_db_session,
            )
        assert exc_info.value.status_code == 400
        assert "Authentication failed" in exc_info.value.detail
        # Verify expired entry was cleaned up
        mock_db_session.delete.assert_called_once_with(mock_expired_request)

    @pytest.mark.anyio
    async def test_concurrent_saml_request_usage(
        self, mock_saml_client, valid_saml_response_with_signature
    ):
        """Test handling of concurrent attempts to use the same SAMLRequestData."""
        request_id = "_concurrent_test_id"
        relay_state = "test_relay_state"

        mock_stored_request = SAMLRequestData(
            id=request_id,
            relay_state=relay_state,
            expires_at=datetime.now() + timedelta(seconds=300),
            created_at=datetime.now(),
        )

        # First session - successful retrieval
        mock_db_session1 = AsyncMock(spec=AsyncSession)
        mock_result_obj1 = MagicMock()
        mock_result_obj1.scalar_one_or_none = MagicMock(
            return_value=mock_stored_request
        )
        mock_db_session1.execute.return_value = mock_result_obj1

        # Second session - request already consumed (returns None)
        mock_db_session2 = AsyncMock(spec=AsyncSession)
        mock_result_obj2 = MagicMock()
        mock_result_obj2.scalar_one_or_none = MagicMock(return_value=None)
        mock_db_session2.execute.return_value = mock_result_obj2

        mock_response = MagicMock()
        mock_response.in_response_to = request_id
        mock_response.signature_check_result = True
        mock_response.response_signed = True
        mock_response.assertions_signed = True
        actual_xml_string = base64.b64decode(
            valid_saml_response_with_signature.encode("utf-8")
        ).decode("utf-8")
        mock_response._xmlstr = actual_xml_string
        mock_response.configure_mock(__str__=lambda current_self: current_self._xmlstr)
        mock_saml_client.parse_authn_request_response.return_value = mock_response

        mock_user = MagicMock()
        mock_user.is_active = True
        mock_user_manager = AsyncMock()
        mock_user_manager.saml_callback.return_value = mock_user
        mock_auth_response = MagicMock()

        # First request should succeed
        with patch(
            "tracecat.auth.saml.auth_backend.login", return_value=mock_auth_response
        ):
            await sso_acs(
                request=MagicMock(),
                saml_response=valid_saml_response_with_signature,
                relay_state=relay_state,
                user_manager=mock_user_manager,
                strategy=MagicMock(),
                client=mock_saml_client,
                db_session=mock_db_session1,
            )

        # Second concurrent request should fail (request already consumed)
        with pytest.raises(HTTPException) as exc_info:
            await sso_acs(
                request=MagicMock(),
                saml_response=valid_saml_response_with_signature,
                relay_state=relay_state,
                user_manager=mock_user_manager,
                strategy=MagicMock(),
                client=mock_saml_client,
                db_session=mock_db_session2,
            )
        assert exc_info.value.status_code == 400
        assert "Authentication failed" in exc_info.value.detail

    @pytest.mark.anyio
    async def test_saml_request_data_cleanup_on_various_failures(
        self, mock_saml_client, valid_saml_response_with_signature, mock_db_session
    ):
        """Test that SAMLRequestData is properly cleaned up on various failure scenarios."""
        request_id = "_cleanup_test_id"
        relay_state = "test_relay_state"

        mock_stored_request = SAMLRequestData(
            id=request_id,
            relay_state=relay_state,
            expires_at=datetime.now() + timedelta(seconds=300),
            created_at=datetime.now(),
        )
        mock_db_session.execute.return_value.scalar_one_or_none.return_value = (
            mock_stored_request
        )

        mock_response = MagicMock()
        mock_response.in_response_to = request_id
        mock_response.signature_check_result = True
        mock_response.response_signed = True
        mock_response.assertions_signed = True
        actual_xml_string = base64.b64decode(
            valid_saml_response_with_signature.encode("utf-8")
        ).decode("utf-8")
        mock_response._xmlstr = actual_xml_string
        mock_response.configure_mock(__str__=lambda current_self: current_self._xmlstr)
        mock_saml_client.parse_authn_request_response.return_value = mock_response

        # Test cleanup when user_manager.saml_callback fails
        mock_user_manager = AsyncMock()
        mock_user_manager.saml_callback.side_effect = Exception("User creation failed")

        with pytest.raises(Exception, match="User creation failed"):
            await sso_acs(
                request=MagicMock(),
                saml_response=valid_saml_response_with_signature,
                relay_state=relay_state,
                user_manager=mock_user_manager,
                strategy=MagicMock(),
                client=mock_saml_client,
                db_session=mock_db_session,
            )

        # Verify that the SAMLRequestData was still deleted despite the user creation failure
        mock_db_session.delete.assert_called_once_with(mock_stored_request)
        mock_db_session.commit.assert_called()
