import contextlib
import os
import uuid
from collections.abc import AsyncGenerator

from fastapi import Depends, Request
from fastapi_users import BaseUserManager, FastAPIUsers, UUIDIDMixin
from fastapi_users.authentication import (
    AuthenticationBackend,
    CookieTransport,
)
from fastapi_users.authentication.strategy.db import (
    AccessTokenDatabase,
    DatabaseStrategy,
)
from fastapi_users.db import SQLAlchemyUserDatabase
from fastapi_users.exceptions import UserAlreadyExists
from httpx_oauth.clients.google import GoogleOAuth2
from sqlalchemy.ext.asyncio import AsyncSession

from tracecat import config
from tracecat.auth.schemas import UserCreate
from tracecat.db.adapter import (
    SQLModelAccessTokenDatabaseAsync,
    SQLModelUserDatabaseAsync,
)
from tracecat.db.engine import get_async_session, get_async_session_context_manager
from tracecat.db.schemas import AccessToken, OAuthAccount, User
from tracecat.logging import logger

google_oauth_client = GoogleOAuth2(
    os.getenv("GOOGLE_OAUTH_CLIENT_ID", ""),
    os.getenv("GOOGLE_OAUTH_CLIENT_SECRET", ""),
)


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


async def get_user_db(session: AsyncSession = Depends(get_async_session)):
    yield SQLAlchemyUserDatabase(session, User, OAuthAccount)


async def get_access_token_db(
    session: AsyncSession = Depends(get_async_session),
) -> AsyncGenerator[SQLModelAccessTokenDatabaseAsync, None]:
    yield SQLModelAccessTokenDatabaseAsync(session, AccessToken)  # type: ignore


def get_user_db_context(
    session: AsyncSession,
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


fastapi_users = FastAPIUsers[User, uuid.UUID](get_user_manager, [auth_backend])

current_active_user = fastapi_users.current_user(active=True)


async def create_user(params: UserCreate, exist_ok: bool = True) -> User | None:
    try:
        async with get_async_session_context_manager() as session:
            async with get_user_db_context(session) as user_db:
                async with get_user_manager_context(user_db) as user_manager:
                    user = await user_manager.create(params)
                    logger.info(f"User created {user}")
                    return user
    except UserAlreadyExists:
        logger.warning(f"User {params.email} already exists")
        if not exist_ok:
            raise
        return None


async def get_user_db_sqlmodel(session: AsyncSession = Depends(get_async_session)):
    yield SQLModelUserDatabaseAsync(session, User, OAuthAccount)
