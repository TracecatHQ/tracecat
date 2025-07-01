from fastapi import APIRouter, Depends, HTTPException, Query, Request, status
import uuid
from fastapi_users import models
from fastapi_users.authentication import AuthenticationBackend, Strategy
from fastapi_users.exceptions import UserAlreadyExists
from fastapi_users.jwt import SecretType, decode_jwt
from fastapi_users.manager import BaseUserManager, UserManagerDependency
from fastapi_users.router.oauth import (
    OAuth2AuthorizeCallback,
    OAuth2AuthorizeResponse,
    OAuth2Token,
    generate_state_token,
    STATE_TOKEN_AUDIENCE,
)
from httpx_oauth.clients.openid import OpenID
from fastapi_users.router.common import ErrorModel, ErrorCode

from tracecat.logger import logger

GENERIC_OIDC_ERROR = "Authentication failed. Please try again or contact support."


def get_oidc_router(
    openid_client: OpenID,
    backend: AuthenticationBackend[models.UP, models.ID],
    get_user_manager: UserManagerDependency[models.UP, models.ID],
    state_secret: SecretType,
    *,
    redirect_url: str,
    associate_by_email: bool = True,
    is_verified_by_default: bool = True,
) -> APIRouter:
    """Generate an OIDC authentication router with structured logging."""

    router = APIRouter()
    callback_route_name = f"oidc:{backend.name}.callback"

    oauth2_authorize_callback = OAuth2AuthorizeCallback(
        openid_client, redirect_url=redirect_url
    )

    log = logger.bind(
        auth_flow="oidc",
        client_id=openid_client.client_id,
        issuer=openid_client.openid_configuration.get("issuer"),
    )

    @router.get(
        "/authorize",
        name=f"oidc:{backend.name}.authorize",
        response_model=OAuth2AuthorizeResponse,
    )
    async def authorize(
        request: Request, scopes: list[str] = Query(None)
    ) -> OAuth2AuthorizeResponse:
        req_log = log.bind(request_id=request.headers.get("X-Request-ID") or str(uuid.uuid4()))
        req_log.info("login_started")
        try:
            state = generate_state_token({}, state_secret)
            authorization_url = await openid_client.get_authorization_url(
                redirect_url,
                state,
                scopes,
            )
        except Exception as e:  # pragma: no cover - network failures
            req_log.error(
                "authorize_failed",
                error=str(e),
                exc_info=e,
            )
            raise HTTPException(
                status_code=status.HTTP_502_BAD_GATEWAY,
                detail=GENERIC_OIDC_ERROR,
            ) from e
        req_log.info("redirect_sent", redirect_url=authorization_url)
        return OAuth2AuthorizeResponse(authorization_url=authorization_url)

    @router.get(
        "/callback",
        name=callback_route_name,
        description="The response varies based on the authentication backend used.",
        responses={
            status.HTTP_400_BAD_REQUEST: {
                "model": ErrorModel,
                "content": {
                    "application/json": {
                        "examples": {
                            "INVALID_STATE_TOKEN": {
                                "summary": "Invalid state token.",
                                "value": None,
                            },
                            ErrorCode.LOGIN_BAD_CREDENTIALS: {
                                "summary": "User is inactive.",
                                "value": {"detail": ErrorCode.LOGIN_BAD_CREDENTIALS},
                            },
                        }
                    }
                },
            }
        },
    )
    async def callback(
        request: Request,
        access_token_state: tuple[OAuth2Token, str] = Depends(
            oauth2_authorize_callback
        ),
        user_manager: BaseUserManager[models.UP, models.ID] = Depends(get_user_manager),
        strategy: Strategy[models.UP, models.ID] = Depends(backend.get_strategy),
    ):
        token, state = access_token_state
        req_log = log.bind(request_id=request.headers.get("X-Request-ID") or str(uuid.uuid4()))
        req_log.info("callback_received", state=state)
        try:
            sub, email = await openid_client.get_id_email(token["access_token"])
            req_log.info("userinfo_fetched", sub=sub, email=email)
        except Exception as e:  # pragma: no cover - network failures
            req_log.error("userinfo_failed", error=str(e), exc_info=e)
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail=GENERIC_OIDC_ERROR,
            ) from e

        if email is None:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail=GENERIC_OIDC_ERROR,
            )

        try:
            decode_jwt(state, state_secret, [STATE_TOKEN_AUDIENCE])
        except Exception as e:
            req_log.error("invalid_state_token", error=str(e))
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail=GENERIC_OIDC_ERROR,
            ) from e

        try:
            user = await user_manager.oauth_callback(
                openid_client.name,
                token["access_token"],
                sub,
                email,
                token.get("expires_at"),
                token.get("refresh_token"),
                request,
                associate_by_email=associate_by_email,
                is_verified_by_default=is_verified_by_default,
            )
            req_log.info("token_exchanged", sub=sub, email=email)
        except UserAlreadyExists as e:
            req_log.error("user_exists", email=email)
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail=GENERIC_OIDC_ERROR,
            ) from e
        except Exception as e:
            req_log.error("user_callback_failed", error=str(e), exc_info=e)
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail=GENERIC_OIDC_ERROR,
            ) from e

        if not user.is_active:
            req_log.error("inactive_user", email=email)
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail=GENERIC_OIDC_ERROR,
            )

        response = await backend.login(strategy, user)
        await user_manager.on_after_login(user, request, response)
        req_log.info("user_authenticated", sub=sub, email=email)
        return response

    return router
