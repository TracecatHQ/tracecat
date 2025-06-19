import contextlib
import uuid
from collections.abc import AsyncGenerator, Iterable, Sequence
from datetime import UTC, datetime
from typing import Annotated, cast

from fastapi import APIRouter, Depends, Request, Response, status
from fastapi_users import (
    BaseUserManager,
    FastAPIUsers,
    InvalidPasswordException,
    UUIDIDMixin,
    models,
)
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
from fastapi_users.exceptions import (
    FastAPIUsersException,
    UserAlreadyExists,
    UserNotExists,
)
from fastapi_users.openapi import OpenAPIResponseType
from pydantic import EmailStr
from sqlalchemy.ext.asyncio import AsyncSession as SQLAlchemyAsyncSession
from sqlmodel import col, select
from sqlmodel.ext.asyncio.session import AsyncSession as SQLModelAsyncSession

from tracecat import config
from tracecat.api.common import bootstrap_role
from tracecat.auth.models import UserCreate, UserRole, UserUpdate
from tracecat.authz.models import WorkspaceRole
from tracecat.authz.service import MembershipService
from tracecat.contexts import ctx_role
from tracecat.db.adapter import (
    SQLModelAccessTokenDatabaseAsync,
    SQLModelUserDatabaseAsync,
)
from tracecat.db.engine import get_async_session, get_async_session_context_manager
from tracecat.db.schemas import AccessToken, OAuthAccount, User
from tracecat.logger import logger
from tracecat.settings.service import get_setting
from tracecat.types.auth import AccessLevel, system_role
from tracecat.workspaces.models import WorkspaceMembershipCreate
from tracecat.workspaces.service import WorkspaceService


class InvalidEmailException(FastAPIUsersException):
    """Exception raised on registration with an invalid email."""

    def __init__(self) -> None:
        super().__init__("Please enter a valid email address.")


class PermissionsException(FastAPIUsersException):
    """Exception raised on permissions error."""


class UserManager(UUIDIDMixin, BaseUserManager[User, uuid.UUID]):
    reset_password_token_secret = config.USER_AUTH_SECRET
    verification_token_secret = config.USER_AUTH_SECRET

    def __init__(self, user_db: SQLAlchemyUserDatabase) -> None:
        super().__init__(user_db)
        self.logger = logger.bind(unit="UserManager")
        self.role = bootstrap_role()

    async def update(
        self,
        user_update: UserUpdate,
        user: User,
        safe: bool = False,
        request: Request | None = None,
    ):
        """Update a user with user privileges."""
        # NOTE(security): Prevent unprivileged users from changing role or is_superuser fields
        blacklist = ("role", "is_superuser")
        set_fields = user_update.model_fields_set

        role = ctx_role.get()
        is_unprivileged = role is not None and role.access_level != AccessLevel.ADMIN
        if not role or (
            # Not admin and trying to change role or is_superuser
            is_unprivileged and any(field in set_fields for field in blacklist)
        ):
            raise PermissionsException("Operation not permitted")

        return await super().update(user_update, user, safe=True, request=request)

    async def admin_update(
        self,
        user_update: UserUpdate,
        user: User,
        request: Request | None = None,
    ):
        """Update a user with admin privileges. This is only used to bootstrap the first user."""
        return await super().update(user_update, user, safe=False, request=request)

    async def validate_password(self, password: str, user: User) -> None:
        if len(password) < config.TRACECAT__AUTH_MIN_PASSWORD_LENGTH:
            raise InvalidPasswordException(
                f"Password must be at least {config.TRACECAT__AUTH_MIN_PASSWORD_LENGTH} characters long"
            )

    async def validate_email(self, email: str) -> None:
        # Check if this is attempting to be the first user (superadmin)
        async with get_async_session_context_manager() as session:
            users = await list_users(session=session)
            if len(users) == 0:  # This would be the first user
                # Only allow registration if this is the designated superadmin email
                if not config.TRACECAT__AUTH_SUPERADMIN_EMAIL:
                    self.logger.error(
                        "No superadmin email configured, but attempting first user registration"
                    )
                    raise InvalidEmailException()
                if email != config.TRACECAT__AUTH_SUPERADMIN_EMAIL:
                    self.logger.error(
                        "First user registration attempted with non-superadmin email",
                        attempted_email=email,
                        expected_email=config.TRACECAT__AUTH_SUPERADMIN_EMAIL,
                    )
                    raise InvalidEmailException()
                self.logger.info(
                    "Allowing first user registration for superadmin email", email=email
                )
                return

        # For non-first users, apply normal domain validation
        allowed_domains = cast(
            list[str] | None,
            await get_setting("auth_allowed_email_domains", role=self.role),
            # Allow overriding of empty list
        ) or list(config.TRACECAT__AUTH_ALLOWED_DOMAINS)
        self.logger.debug("Allowed domains", allowed_domains=allowed_domains)
        validate_email(email=email, allowed_domains=allowed_domains)

    async def oauth_callback(
        self,
        oauth_name: str,
        access_token: str,
        account_id: str,
        account_email: str,
        expires_at: int | None = None,
        refresh_token: str | None = None,
        request: Request | None = None,
        *,
        associate_by_email: bool = False,
        is_verified_by_default: bool = False,
    ) -> User:
        await self.validate_email(account_email)
        return await super().oauth_callback(  # type: ignore
            oauth_name,
            access_token,
            account_id,
            account_email,
            expires_at,
            refresh_token,
            request,
            associate_by_email=associate_by_email,
            is_verified_by_default=is_verified_by_default,
        )

    async def create(
        self,
        user_create: UserCreate,
        safe: bool = False,
        request: Request | None = None,
    ) -> User:
        await self.validate_email(user_create.email)
        try:
            return await super().create(user_create, safe, request)
        except UserAlreadyExists:
            # NOTE(security): Bypass fastapi users exception handler
            raise InvalidEmailException() from None

    async def on_after_login(
        self,
        user: User,
        request: Request | None = None,
        response: Response | None = None,
    ) -> None:
        # Update last login info
        try:
            now = datetime.now(UTC)
            await self.user_db.update(user, update_dict={"last_login_at": now})
        except Exception as e:
            self.logger.warning(
                "Failed to update last login info",
                user_id=user.id,
                user=user.email,
                error=e,
            )

    async def on_after_register(
        self, user: User, request: Request | None = None
    ) -> None:
        self.logger.info(f"User {user.id} has registered.")

        # Check if this user should be promoted to superuser
        async with get_async_session_context_manager() as session:
            users = await list_users(session=session)
            superadmin_email = config.TRACECAT__AUTH_SUPERADMIN_EMAIL
            if len(users) == 1 and superadmin_email and user.email == superadmin_email:
                # This is the first user and matches the designated superadmin email
                update_params = UserUpdate(is_superuser=True, role=UserRole.ADMIN)
                # NOTE(security): Bypass safety to create sueradmin
                await self.admin_update(user_update=update_params, user=user)
                self.logger.info("First user promoted to superadmin", email=user.email)

            elif len(users) > 1 and await get_setting(
                "app_create_workspace_on_register", default=True
            ):
                # Check if we should auto-create a workspace for the user
                self.logger.info("Creating workspace for new user", user=user.email)
                try:
                    # Determine workspace name
                    if user.first_name:
                        workspace_name = f"{user.first_name}'s Workspace"
                    else:
                        # Remove domain from email to use as workspace name
                        email_username = user.email.split("@")[0]
                        workspace_name = f"{email_username}'s Workspace"

                    # Create workspace with the system role
                    sys_role = system_role()
                    ws_svc = WorkspaceService(session, role=sys_role)
                    workspace = await ws_svc.create_workspace(
                        name=workspace_name, users=[user]
                    )
                    # Add user to workspace as a workspace admin
                    membership_svc = MembershipService(session, role=sys_role)
                    await membership_svc.create_membership(
                        workspace_id=workspace.id,
                        params=WorkspaceMembershipCreate(
                            user_id=user.id, role=WorkspaceRole.ADMIN
                        ),
                    )
                    self.logger.info(
                        "Created workspace for new user",
                        workspace_id=workspace.id,
                        workspace_name=workspace_name,
                        user_id=user.id,
                        user_email=user.email,
                    )
                except Exception as e:
                    self.logger.error(
                        "Failed to create workspace for new user",
                        error=str(e),
                        user_id=user.id,
                        user_email=user.email,
                    )

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

    async def saml_callback(
        self,
        *,
        email: str,
        associate_by_email: bool = True,
        is_verified_by_default: bool = True,
    ) -> User:
        """
        Handle the callback after a successful SAML authentication.

        :param email: Email of the user from SAML response.
        :param associate_by_email: If True, associate existing user with the same email. Defaults to True.
        :param is_verified_by_default: If True, set is_verified flag for new users. Defaults to True.
        :return: A user.
        """
        await self.validate_email(email)
        try:
            user = await self.get_by_email(email)
            if not associate_by_email:
                raise UserAlreadyExists()
        except UserNotExists:
            # Create account
            password = self.password_helper.generate()
            user_dict = {
                "email": email,
                "hashed_password": self.password_helper.hash(password),
                "is_verified": is_verified_by_default,
            }
            user = await self.user_db.create(user_dict)
            await self.on_after_register(user)

        self.logger.info(f"User {user.id} authenticated via SAML.")
        return user


async def get_user_db(session: SQLAlchemyAsyncSession = Depends(get_async_session)):
    yield SQLAlchemyUserDatabase(session, User, OAuthAccount)  # type: ignore


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

AuthBackendStrategyDep = Annotated[
    Strategy[models.UP, models.ID], Depends(auth_backend.get_strategy)
]
UserManagerDep = Annotated[UserManager, Depends(get_user_manager)]


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


def is_unprivileged(user: User) -> bool:
    """Check if a user is not privileged (i.e. not an admin or superuser)."""
    return user.role != UserRole.ADMIN and not user.is_superuser


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


async def list_users(*, session: SQLModelAsyncSession) -> Sequence[User]:
    statement = select(User)
    result = await session.exec(statement)
    return result.all()


async def search_users(
    *,
    session: SQLModelAsyncSession,
    user_ids: Iterable[uuid.UUID] | None = None,
) -> Sequence[User]:
    statement = select(User)
    if user_ids:
        statement = statement.where(col(User.id).in_(user_ids))
    result = await session.exec(statement)
    return result.all()


def validate_email(
    email: EmailStr, *, allowed_domains: list[str] | None = None
) -> None:
    # Safety: This is already a validated email, so we can split on the first @
    _, domain = email.split("@", 1)
    logger.info(f"Domain: {domain}")

    if allowed_domains and domain not in allowed_domains:
        raise InvalidEmailException()
