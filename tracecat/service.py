"""Base service classes for Tracecat.

Role Semantics
--------------
The `role` parameter in service classes represents the **operator context** - the entity
(user or service) performing the action. It is NOT the subject or target of the operation.

For example, when an admin user updates another user's profile:
- The `role` is the admin user (the operator performing the action)
- The target user is passed as a separate parameter to the method

This distinction is important for:
- Authorization: Checking if the operator has permission to perform the action
- Audit logging: Recording who performed the action
- Multi-tenancy: Scoping operations to the operator's organization/workspace

The role is typically injected via FastAPI dependencies at the router level and propagated
to services. Services can also resolve the role from the `ctx_role` context variable.

Choosing a Base Class
---------------------
+----------------------+--------------+------------------+---------------------------------------+
| Base Class           | Scoped To    | Operator Context | Use When                              |
+----------------------+--------------+------------------+---------------------------------------+
| BaseService          | Nothing      | None             | Internal utilities, background jobs,  |
|                      |              |                  | read-only lookups without audit needs |
+----------------------+--------------+------------------+---------------------------------------+
| BaseOrgService       | Organization | Role             | Operations on org-level resources     |
|                      |              |                  | (org settings, memberships)           |
+----------------------+--------------+------------------+---------------------------------------+
| BaseWorkspaceService | Workspace    | Role             | Operations on workspace resources     |
|                      |              |                  | (workflows, cases, tables)            |
+----------------------+--------------+------------------+---------------------------------------+
| BasePlatformService  | Platform     | PlatformRole     | Superuser operations across all       |
|                      | (global)     |                  | orgs/workspaces                       |
+----------------------+--------------+------------------+---------------------------------------+

BasePlatformService is specifically for:
- Cross-org operations: Managing multiple organizations (create/delete orgs, assign tiers)
- Platform-global resources: Tiers, platform settings, global registry
- Superuser-only access: Requires SuperuserRole dependency
- Audit trail needed: Preserves who (superuser) performed the action
- Not scoped to caller's org: The operator acts on resources they don't "belong" to
"""

from __future__ import annotations

from collections.abc import AsyncGenerator, Callable
from contextlib import asynccontextmanager
from typing import TYPE_CHECKING, Any, ClassVar, Self

from tracecat.contexts import ctx_role
from tracecat.db.engine import get_async_session_context_manager
from tracecat.exceptions import TracecatAuthorizationError
from tracecat.identifiers import OrganizationID, WorkspaceID
from tracecat.logger import logger

if TYPE_CHECKING:
    from sqlalchemy.ext.asyncio import AsyncSession

    from tracecat.auth.types import PlatformRole, Role


class BaseService:
    """Base class for services with no role or org/workspace context.

    Use this for:
    - Internal utilities and helpers (e.g., TierService for entitlement lookups)
    - Background jobs that don't need operator attribution
    - Read-only lookups that don't require audit trails

    If you need operator context for authorization or audit logging, use one of:
    - BaseOrgService: For org-scoped operations
    - BaseWorkspaceService: For workspace-scoped operations
    - BasePlatformService: For superuser/platform admin operations
    """

    service_name: ClassVar[str]

    def __init__(self, session: AsyncSession):
        self.session = session
        self.logger = logger.bind(service=self.service_name)

    @classmethod
    @asynccontextmanager
    async def with_session(
        cls,
        *,
        session: AsyncSession | None = None,
    ) -> AsyncGenerator[Self, None]:
        """Create a service instance with a database session.

        Args:
            session: Optional existing session. If provided, caller is responsible
                for managing its lifecycle (it won't be closed by this context manager).
        """
        if session is not None:
            yield cls(session)
        else:
            async with get_async_session_context_manager() as session:
                yield cls(session)

    @classmethod
    def get_activities(cls) -> list[Callable[..., Any]]:
        """Get all temporal activities in the class."""
        return [
            getattr(cls, method_name)
            for method_name in dir(cls)
            if hasattr(getattr(cls, method_name), "__temporal_activity_definition")
        ]


class BasePlatformService(BaseService):
    """Base class for platform-wide superuser operations.

    Use this for operations that span across or are outside the org/workspace hierarchy:
    - Cross-org operations: Managing multiple organizations (create/delete orgs)
    - Platform-global resources: Tiers, platform settings, global registry
    - User management across organizations

    Services extending this class require a PlatformRole (from SuperuserRole dependency).
    This preserves operator context for audit logging while not being scoped to any
    specific organization or workspace.
    """

    role: PlatformRole  # Always non-None for platform services

    def __init__(self, session: AsyncSession, role: PlatformRole):
        super().__init__(session)
        self.role = role


class BaseOrgService(BaseService):
    """Base class for services scoped to an organization.

    Use this for operations on org-level resources such as:
    - Organization settings and configuration
    - Org membership management
    - Resources shared across all workspaces in an org

    Services extending this class require a Role with organization_id.
    The role is resolved from the parameter or ctx_role context variable.

    The role represents the operator (the user/service performing the action),
    not the subject or target of the operation. See module docstring for details.
    """

    role: Role  # Always non-None for org services
    organization_id: OrganizationID  # Always non-None after __init__

    def __init__(self, session: AsyncSession, role: Role | None = None):
        super().__init__(session)
        resolved_role = role or ctx_role.get()
        if resolved_role is None:
            raise TracecatAuthorizationError(
                f"{self.service_name} service requires organization context"
            )
        if resolved_role.organization_id is None:
            raise TracecatAuthorizationError(
                f"{self.service_name} service requires organization_id in role"
            )
        self.role = resolved_role
        self.organization_id = resolved_role.organization_id

    @classmethod
    @asynccontextmanager
    async def with_session(
        cls,
        role: Role | None = None,
        *,
        session: AsyncSession | None = None,
    ) -> AsyncGenerator[Self, None]:
        """Create a service instance with a database session.

        Args:
            role: Optional role for authorization context. Falls back to ctx_role.
            session: Optional existing session. If provided, caller is responsible
                for managing its lifecycle (it won't be closed by this context manager).
        """
        if session is not None:
            yield cls(session, role=role)
        else:
            async with get_async_session_context_manager() as session:
                yield cls(session, role=role)


class BaseWorkspaceService(BaseOrgService):
    """Base class for services scoped to a workspace.

    Use this for operations on workspace-level resources such as:
    - Workflows and workflow executions
    - Cases and case management
    - Tables and custom fields
    - Secrets and credentials

    Services extending this class require a Role with both organization_id
    and workspace_id. Most user-facing operations use this base class.
    """

    workspace_id: WorkspaceID  # Always non-None after __init__

    def __init__(self, session: AsyncSession, role: Role | None = None):
        super().__init__(session, role)
        # Note: role None check is handled by BaseOrgService
        if self.role.workspace_id is None:
            raise TracecatAuthorizationError(
                f"{self.service_name} service requires workspace"
            )
        self.workspace_id = self.role.workspace_id
