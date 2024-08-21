import contextlib
import uuid
from collections.abc import AsyncGenerator, Awaitable

from fastapi import APIRouter, Depends, Request, Response, status
from fastapi_users import BaseUserManager, FastAPIUsers, UUIDIDMixin, models
from fastapi_users.authentication import (
    AuthenticationBackend,
    CookieTransport,
    Strategy,
)
from fastapi_users.authentication.strategy.db import (
    AccessTokenDatabase,
    DatabaseStrategy,
)
from fastapi_users.db import SQLAlchemyUserDatabase
from fastapi_users.exceptions import UserAlreadyExists
from fastapi_users.openapi import OpenAPIResponseType
from sqlalchemy.ext.asyncio import AsyncSession as SQLAlchemyAsyncSession
from sqlmodel import select
from sqlmodel.ext.asyncio.session import AsyncSession as SQLModelAsyncSession

from tracecat import config
from tracecat.auth.schemas import UserCreate, UserRole
from tracecat.db.adapter import (
    SQLModelAccessTokenDatabaseAsync,
    SQLModelUserDatabaseAsync,
)
from tracecat.db.engine import get_async_session, get_async_session_context_manager
from tracecat.db.schemas import AccessToken, OAuthAccount, User
from tracecat.logging import logger


class UserManager(UUIDIDMixin, BaseUserManager[User, uuid.UUID]):
    reset_password_token_secret = config.USER_AUTH_SECRET
    verification_token_secret = config.USER_AUTH_SECRET

    def __init__(self, user_db: SQLAlchemyUserDatabase) -> None:
        super().__init__(user_db)
        self.logger = logger.bind(unit="UserManager")

    async def on_after_register(
        self, user: User, request: Request | None = None
    ) -> None:
        self.logger.info(f"User {user.id} has registered.")

    async def on_after_forgot_password(
        self, user: User, token: str, request: Request | None = None
    ) -> None:
        self.logger.info(
            f"User {user.id} has forgot their password. Reset token: {token}"
        )

    async def on_after_request_verify(
        self, user: User, token: str, request: Request | None = None
    ) -> None:
        self.logger.info(
            f"Verification requested for user {user.id}. Verification token: {token}"
        )


async def get_user_db(session: SQLAlchemyAsyncSession = Depends(get_async_session)):
    yield SQLAlchemyUserDatabase(session, User, OAuthAccount)


async def get_access_token_db(
    session: SQLAlchemyAsyncSession = Depends(get_async_session),
) -> AsyncGenerator[SQLModelAccessTokenDatabaseAsync, None]:
    yield SQLModelAccessTokenDatabaseAsync(session, AccessToken)  # type: ignore


def get_user_db_context(
    session: SQLAlchemyAsyncSession,
) -> contextlib.AbstractAsyncContextManager[SQLAlchemyUserDatabase]:
    return contextlib.asynccontextmanager(get_user_db)(session=session)


async def get_user_manager(
    user_db: SQLAlchemyUserDatabase = Depends(get_user_db),
) -> AsyncGenerator[UserManager, None]:
    yield UserManager(user_db)


def get_user_manager_context(
    user_db: SQLAlchemyUserDatabase,
) -> contextlib.AbstractAsyncContextManager[UserManager]:
    return contextlib.asynccontextmanager(get_user_manager)(user_db=user_db)


cookie_transport = CookieTransport(
    cookie_max_age=config.SESSION_EXPIRE_TIME_SECONDS,
    cookie_secure=config.TRACECAT__API_URL.startswith("https"),
)


def get_database_strategy(
    access_token_db: AccessTokenDatabase[AccessToken] = Depends(get_access_token_db),
) -> DatabaseStrategy:
    strategy = DatabaseStrategy(
        access_token_db,
        lifetime_seconds=config.SESSION_EXPIRE_TIME_SECONDS,  # type: ignore
    )

    return strategy


auth_backend = AuthenticationBackend(
    name="database",
    transport=cookie_transport,
    get_strategy=get_database_strategy,
)


class FastAPIUserWithLogoutRouter(FastAPIUsers[models.UP, models.ID]):
    def get_logout_router(
        self,
        backend: AuthenticationBackend,
        requires_verification: bool = config.TRACECAT__AUTH_REQUIRE_EMAIL_VERIFICATION,
    ) -> APIRouter:
        """
        Provide a router for logout only for OAuth/OIDC Flows.
        This way the login router does not need to be included
        """
        router = APIRouter()
        get_current_user_token = self.authenticator.current_user_token(
            active=True, verified=requires_verification
        )
        logout_responses: OpenAPIResponseType = {
            **{
                status.HTTP_401_UNAUTHORIZED: {
                    "description": "Missing token or inactive user."
                }
            },
            **backend.transport.get_openapi_logout_responses_success(),
        }

        @router.post(
            "/logout", name=f"auth:{backend.name}.logout", responses=logout_responses
        )
        async def logout(
            user_token: tuple[models.UP, str] = Depends(get_current_user_token),
            strategy: Strategy[models.UP, models.ID] = Depends(backend.get_strategy),
        ) -> Response:
            user, token = user_token
            return await backend.logout(strategy, user, token)

        return router


fastapi_users = FastAPIUserWithLogoutRouter[User, uuid.UUID](
    get_user_manager, [auth_backend]
)

current_active_user = fastapi_users.current_user(active=True)
optional_current_active_user = fastapi_users.current_user(active=True, optional=True)


async def get_or_create_user(params: UserCreate, exist_ok: bool = True) -> User:
    async with get_async_session_context_manager() as session:
        async with get_user_db_context(session) as user_db:
            async with get_user_manager_context(user_db) as user_manager:
                try:
                    user = await user_manager.create(params)
                    logger.info(f"User created {user}")
                    return user
                except UserAlreadyExists:
                    # Compares by email
                    logger.warning(f"User {params.email} already exists")
                    if not exist_ok:
                        raise
                    return await user_manager.get_by_email(params.email)


async def get_user_db_sqlmodel(
    session: SQLAlchemyAsyncSession = Depends(get_async_session),
):
    yield SQLModelUserDatabaseAsync(session, User, OAuthAccount)


def get_or_create_default_admin_user() -> Awaitable[User]:
    return get_or_create_user(default_admin_user(), exist_ok=True)


async def list_users(*, session: SQLModelAsyncSession) -> list[User]:
    statement = select(User)
    result = await session.exec(statement)
    return result.all()


def default_admin_user() -> UserCreate:
    return UserCreate(
        email="admin@domain.com",
        first_name="Admin",
        last_name="User",
        password="password",
        is_superuser=True,
        is_verified=True,
        role=UserRole.ADMIN,
    )
