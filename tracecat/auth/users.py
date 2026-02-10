import contextlib
import hashlib
import os
import uuid
from collections.abc import AsyncGenerator, Iterable, Sequence
from datetime import UTC, datetime
from typing import Annotated, cast

from fastapi import APIRouter, Depends, Request, Response, status
from fastapi.security import OAuth2PasswordRequestForm
from fastapi_users import (
    BaseUserManager,
    FastAPIUsers,
    InvalidPasswordException,
    UUIDIDMixin,
    models,
    schemas,
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
from fastapi_users_db_sqlalchemy.access_token import SQLAlchemyAccessTokenDatabase
from pydantic import EmailStr
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from tracecat import config
from tracecat.api.common import bootstrap_role
from tracecat.audit.service import AuditService
from tracecat.auth.enums import AuthType
from tracecat.auth.schemas import UserCreate, UserUpdate
from tracecat.auth.types import PlatformRole
from tracecat.contexts import ctx_role
from tracecat.db.engine import get_async_session, get_async_session_context_manager
from tracecat.db.models import (
    AccessToken,
    OAuthAccount,
    OrganizationDomain,
    OrganizationMembership,
    User,
)
from tracecat.exceptions import TracecatAuthorizationError, TracecatNotFoundError
from tracecat.identifiers import OrganizationID
from tracecat.logger import logger
from tracecat.organization.domains import normalize_domain
from tracecat.settings.service import get_setting


class InvalidEmailException(FastAPIUsersException):
    """Exception raised on registration with an invalid email."""

    def __init__(self) -> None:
        super().__init__("Please enter a valid email address.")


class PermissionsException(FastAPIUsersException):
    """Exception raised on permissions error."""


class UserManager(UUIDIDMixin, BaseUserManager[User, uuid.UUID]):
    reset_password_token_secret = config.USER_AUTH_SECRET
    verification_token_secret = config.USER_AUTH_SECRET

    def __init__(self, user_db: SQLAlchemyUserDatabase[User, uuid.UUID]) -> None:
        super().__init__(user_db)
        self._user_db = user_db
        self.logger = logger.bind(unit="UserManager")
        self.role = bootstrap_role()
        # Store invitation token between create() and on_after_register()
        self._pending_invitation_token: str | None = None

    async def update(
        self,
        user_update: schemas.BaseUserUpdate,
        user: User,
        safe: bool = False,
        request: Request | None = None,
    ) -> User:
        """Update a user with user privileges."""
        # NOTE(security): Prevent unprivileged users from changing role or is_superuser fields
        denylist = ("role", "is_superuser")
        set_fields = user_update.model_fields_set

        role = ctx_role.get()
        is_unprivileged = role is not None and not role.is_privileged
        if not role or (
            # Not privileged and trying to change role or is_superuser
            is_unprivileged and any(field in set_fields for field in denylist)
        ):
            raise PermissionsException("Operation not permitted")

        return await super().update(user_update, user, safe=True, request=request)

    async def admin_update(
        self,
        user_update: schemas.BaseUserUpdate,
        user: User,
        request: Request | None = None,
    ) -> User:
        """Update a user with admin privileges. This is only used to bootstrap the first user."""
        return await super().update(user_update, user, safe=False, request=request)

    async def validate_password(
        self, password: str, user: schemas.BaseUserCreate | User
    ) -> None:
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

    async def authenticate(self, credentials: OAuth2PasswordRequestForm) -> User | None:
        """Authenticate local email/password and enforce platform/org policy."""
        user = await super().authenticate(credentials)
        if user is None:
            return None
        if await self._is_local_password_login_allowed(user):
            return user
        self.logger.info(
            "Blocked local email/password login by auth policy",
            user_id=str(user.id),
            email=user.email,
        )
        return None

    async def _is_local_password_login_allowed(self, user: User) -> bool:
        if AuthType.BASIC not in config.TRACECAT__AUTH_TYPES:
            return False

        org_ids = await self._list_user_org_ids(user.id)
        if not org_ids:
            return True

        target_org_id = await self._resolve_target_org_for_email(user.email, org_ids)
        if target_org_id is not None:
            # Even with a domain-matched org, block local auth if any membership
            # enforces SAML to avoid cross-org policy bypass.
            return not await self._any_org_saml_enforced(org_ids)

        if len(org_ids) == 1:
            return not await self._is_org_saml_enforced(next(iter(org_ids)))

        # If org is ambiguous (multi-org user with no matching domain), block if any
        # org membership enforces SAML to avoid bypassing org login policy.
        return not await self._any_org_saml_enforced(org_ids)

    async def _list_user_org_ids(self, user_id: uuid.UUID) -> set[OrganizationID]:
        statement = select(OrganizationMembership.organization_id).where(
            OrganizationMembership.user_id == user_id
        )
        result = await self._user_db.session.execute(statement)
        return set(result.scalars().all())

    async def _resolve_target_org_for_email(
        self, email: str, org_ids: set[OrganizationID]
    ) -> OrganizationID | None:
        _, _, email_domain = email.rpartition("@")
        if not email_domain:
            return None

        try:
            normalized_domain = normalize_domain(email_domain).normalized_domain
        except ValueError:
            return None

        statement = select(OrganizationDomain.organization_id).where(
            OrganizationDomain.normalized_domain == normalized_domain,
            OrganizationDomain.is_active.is_(True),
        )
        result = await self._user_db.session.execute(statement)
        organization_id = result.scalar_one_or_none()
        if organization_id is None or organization_id not in org_ids:
            return None
        return organization_id

    async def _is_org_saml_enforced(self, org_id: OrganizationID) -> bool:
        if AuthType.SAML not in config.TRACECAT__AUTH_TYPES:
            return False

        saml_enabled = bool(
            await get_setting(
                "saml_enabled",
                role=bootstrap_role(org_id),
                session=self._user_db.session,
                default=True,
            )
        )
        if not saml_enabled:
            return False

        saml_enforced = await get_setting(
            "saml_enforced",
            role=bootstrap_role(org_id),
            session=self._user_db.session,
            default=False,
        )
        return bool(saml_enforced)

    async def _any_org_saml_enforced(self, org_ids: set[OrganizationID]) -> bool:
        for org_id in org_ids:
            if await self._is_org_saml_enforced(org_id):
                return True
        return False

    async def oauth_callback(  # pyright: ignore[reportIncompatibleMethodOverride]
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
        return await super().oauth_callback(  # pyright: ignore[reportAttributeAccessIssue]
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
        user_create: schemas.BaseUserCreate,
        safe: bool = False,
        request: Request | None = None,
    ) -> User:
        await self.validate_email(user_create.email)

        # Extract and store invitation token for use in on_after_register()
        # This allows atomic invitation acceptance during registration
        if isinstance(user_create, UserCreate) and user_create.invitation_token:
            self._pending_invitation_token = user_create.invitation_token

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
        self.logger.info("User registered", user_id=str(user.id), email=user.email)

        # Log audit event for user registration
        platform_role = PlatformRole(
            type="user", user_id=user.id, service_id="tracecat-api"
        )
        async with AuditService.with_session(role=platform_role) as audit_svc:
            await audit_svc.create_event(
                resource_type="user",
                action="create",
                resource_id=user.id,
            )

        # Promote to superuser if email matches configured superadmin email
        # No count/lock needed - email uniqueness ensures only one user can have this email
        superadmin_email = config.TRACECAT__AUTH_SUPERADMIN_EMAIL
        if superadmin_email and user.email == superadmin_email:
            update_params = UserUpdate(is_superuser=True)
            await self.admin_update(user_update=update_params, user=user)
            self.logger.info("User promoted to superadmin", email=user.email)

        # Accept invitation atomically if token was provided during registration
        # This eliminates race conditions in the invitation flow
        if self._pending_invitation_token:
            await self._accept_invitation_atomically(user)

        # NOTE: We do NOT add users to any organization/workspace here unless invited.
        # - Superusers have implicit access to all orgs (get OrgRole.OWNER in get_role_from_user)
        # - Regular users get org membership via invitation acceptance flow
        # - Workspace membership is managed separately by workspace admins

    async def _accept_invitation_atomically(self, user: User) -> None:
        """Accept an invitation during registration if a token was provided.

        Errors during invitation acceptance are logged but do NOT fail registration.
        This ensures users can still register even if the invitation is invalid/expired.
        """
        # Import here to avoid circular import (organization.service imports from auth.users)
        from tracecat.organization.service import accept_invitation_for_user

        token = self._pending_invitation_token
        self._pending_invitation_token = None  # Clear to prevent reuse

        if not token:
            return

        try:
            async with get_async_session_context_manager() as session:
                membership = await accept_invitation_for_user(
                    session, user_id=user.id, token=token
                )
                self.logger.info(
                    "Invitation accepted during registration",
                    user_id=str(user.id),
                    email=user.email,
                    org_id=str(membership.organization_id),
                )
        except TracecatNotFoundError:
            self.logger.warning(
                "Invitation token not found during registration",
                user_id=str(user.id),
                email=user.email,
            )
        except TracecatAuthorizationError as e:
            self.logger.warning(
                "Invitation acceptance failed during registration",
                user_id=str(user.id),
                email=user.email,
                error=str(e),
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


async def get_user_db(session: AsyncSession = Depends(get_async_session)):
    yield SQLAlchemyUserDatabase(session, User, OAuthAccount)


async def get_access_token_db(
    session: AsyncSession = Depends(get_async_session),
) -> AsyncGenerator[SQLAlchemyAccessTokenDatabase[AccessToken], None]:
    yield SQLAlchemyAccessTokenDatabase(session, AccessToken)


def get_user_db_context(
    session: AsyncSession,
) -> contextlib.AbstractAsyncContextManager[SQLAlchemyUserDatabase[User, uuid.UUID]]:
    return contextlib.asynccontextmanager(get_user_db)(session=session)


async def get_user_manager(
    user_db: SQLAlchemyUserDatabase[User, uuid.UUID] = Depends(get_user_db),
) -> AsyncGenerator[UserManager, None]:
    yield UserManager(user_db)


def get_user_manager_context(
    user_db: SQLAlchemyUserDatabase[User, uuid.UUID],
) -> contextlib.AbstractAsyncContextManager[UserManager]:
    return contextlib.asynccontextmanager(get_user_manager)(user_db=user_db)


def _get_cookie_name() -> str:
    """Get the cookie name, respecting environment variable override or generating a stable one.

    Returns:
        Cookie name from environment variable if set, a stable generated name in development,
        or None to use the default cookie name.
    """
    # Allow explicit override via environment variable (for backward compatibility)
    if env_cookie_name := (
        os.environ.get("TRACECAT__DEV_COOKIE_NAME")
        or os.environ.get("TRACECAT__COOKIE_NAME")
    ):
        return env_cookie_name

    # Only generate stable cookie name in development mode
    if config.TRACECAT__APP_ENV == "development":
        """Generate a stable cookie name unique to this tracecat instance.

        Uses the Docker Compose project name (COMPOSE_PROJECT_NAME) if available,
        which provides a stable, instance-specific identifier. Falls back to a hash
        of stable configuration values if not in a Docker Compose environment.

        This ensures each instance gets a unique cookie name that remains consistent
        across restarts, preventing cookie conflicts when running multiple instances.

        Returns:
            A stable cookie name like 'tracecat_auth_<project_name>' or 'tracecat_auth_<hash>'.
        """
        # Prefer Docker Compose project name (most stable and human-readable)
        from slugify import slugify

        if compose_project_name := os.environ.get("COMPOSE_PROJECT_NAME"):
            return f"tracecat_auth_{slugify(compose_project_name)}"

        # Fallback: use hash of stable instance configuration
        # Use public app URL as primary source of uniqueness (unique per instance)
        # The DB URI and internal API URL are typically the same across Docker instances
        # since they use internal Docker network addresses
        stable_value = config.TRACECAT__PUBLIC_APP_URL

        # Generate a short hash (first 8 characters of SHA256)
        hash_obj = hashlib.sha256(stable_value.encode())
        hash_hex = hash_obj.hexdigest()[:8]

        return f"tracecat_auth_{hash_hex}"

    # In production/staging, use default cookie name (None)
    return "fastapiusersauth"


cookie_transport = CookieTransport(
    cookie_name=_get_cookie_name(),
    cookie_max_age=config.SESSION_EXPIRE_TIME_SECONDS,
    cookie_secure=config.TRACECAT__API_URL.startswith("https"),
)


def get_database_strategy(
    access_token_db: AccessTokenDatabase[AccessToken] = Depends(get_access_token_db),
) -> DatabaseStrategy[User, uuid.UUID, AccessToken]:
    strategy = DatabaseStrategy(
        access_token_db,
        lifetime_seconds=config.SESSION_EXPIRE_TIME_SECONDS,
    )

    return strategy


auth_backend: AuthenticationBackend[User, uuid.UUID] = AuthenticationBackend(
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
        backend: AuthenticationBackend[models.UP, models.ID],
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
        async def logout(  # pyright: ignore[reportUnusedFunction] - registered as FastAPI route handler
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
    """Check if a user is not privileged (i.e. not a superuser)."""
    return not user.is_superuser


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


async def list_users(*, session: AsyncSession) -> Sequence[User]:
    statement = select(User)
    result = await session.execute(statement)
    return result.scalars().all()


async def search_users(
    *,
    session: AsyncSession,
    user_ids: Iterable[uuid.UUID] | None = None,
) -> Sequence[User]:
    statement = select(User)
    if user_ids:
        statement = statement.where(User.id.in_(user_ids))  # pyright: ignore[reportAttributeAccessIssue]
    result = await session.execute(statement)
    return result.scalars().all()


def validate_email(
    email: EmailStr, *, allowed_domains: list[str] | None = None
) -> None:
    # Safety: This is already a validated email, so we can split on the first @
    _, domain = email.split("@", 1)
    if allowed_domains and domain not in allowed_domains:
        raise InvalidEmailException()
    logger.info("Validated email with domain", domain=domain)


async def lookup_user_by_email(*, session: AsyncSession, email: str) -> User | None:
    """Look up a user by their email address.

    Args:
        session: The database session.
        email: The email address to search for.

    Returns:
        User | None: The user object if found, None otherwise.
    """
    statement = select(User).where(User.email == email)  # pyright: ignore[reportArgumentType]
    result = await session.execute(statement)
    return result.scalars().first()
