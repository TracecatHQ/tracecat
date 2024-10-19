import os
from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException, Request, status
from fastapi_users.exceptions import UserAlreadyExists
from ssoready.client import AsyncSSOReady

from tracecat.auth.models import SamlAuthorizeResponse
from tracecat.auth.users import AuthBackendStrategyDep, UserManagerDep, auth_backend
from tracecat.logger import logger

router = APIRouter(prefix="/auth/saml", tags=["auth"])


def get_client():
    base_url = os.getenv("SSOREADY__API_URL")
    api_key = os.getenv("SSOREADY__API_KEY")
    return AsyncSSOReady(api_key=api_key, base_url=base_url)


SamlClient = Annotated[AsyncSSOReady, Depends(get_client)]


@router.get("/authorize", name=f"saml:{auth_backend.name}.authorize")
async def authorize(
    *, client: SamlClient, organization_external_id: str
) -> SamlAuthorizeResponse:
    """Get the SAML redirect URL from the SSO Ready API."""

    logger.debug(
        "Hit SAML authorize", organization_external_id=organization_external_id
    )
    response = await client.saml.get_saml_redirect_url(
        organization_external_id=organization_external_id,
    )
    logger.info("SAML redirect url", redirect_url=response.redirect_url)
    if not response.redirect_url:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="No redirect URL found",
        )
    return SamlAuthorizeResponse(authorization_url=response.redirect_url)


@router.get("/callback", name=f"saml:{auth_backend.name}.callback")
async def callback(
    *,
    request: Request,
    client: SamlClient,
    user_manager: UserManagerDep,
    strategy: AuthBackendStrategyDep,
    saml_access_code: str,
):
    """Redeem the SAML access code."""
    logger.debug("Hit saml callback", saml_access_code=saml_access_code)
    redeem_result = await client.saml.redeem_saml_access_code(
        saml_access_code=saml_access_code,
    )

    logger.info(
        "SAML redeemed successfully",
        result=redeem_result,
    )
    email = redeem_result.email
    if email is None:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="No email found in SAML response",
        )
    org_id = redeem_result.organization_id
    organization_external_id = redeem_result.organization_external_id

    # Try to get the user from the database
    try:
        user = await user_manager.saml_callback(
            email=email,
            organization_id=org_id,
            organization_external_id=organization_external_id,
            request=request,
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
        )

    # Authenticate
    response = await auth_backend.login(strategy, user)
    await user_manager.on_after_login(user, request, response)
    return response
