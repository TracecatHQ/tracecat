"""Characterization tests for core.cases UDFs.

These tests verify the behavior of case UDFs as black boxes, using real database
operations. They serve as regression tests for the SDK migration - the same tests
should pass before and after migration.

Test Strategy:
- No mocks - tests exercise the full path through the service layer
- Tests assert on inputs → outputs of UDFs
- Implementation details (direct service calls vs SDK) are abstracted away
"""

from __future__ import annotations

import base64
import uuid
from datetime import UTC
from typing import get_args

import pytest
import respx
import sqlalchemy as sa
from httpx import ASGITransport
from pydantic import TypeAdapter
from sqlalchemy.ext.asyncio import AsyncSession
from tracecat_registry import types
from tracecat_registry.context import RegistryContext, clear_context, set_context
from tracecat_registry.core.cases import (
    add_case_tag,
    assign_user,
    assign_user_by_email,
    create_case,
    create_comment,
    delete_attachment,
    delete_case,
    download_attachment,
    get_attachment,
    get_attachment_download_url,
    get_case,
    list_attachments,
    list_case_events,
    list_cases,
    list_comments,
    remove_case_tag,
    search_cases,
    update_case,
    update_comment,
    upload_attachment,
)
from tracecat_registry.core.ee.durations import get_case_metrics
from tracecat_registry.core.ee.tasks import create_task, list_tasks
from tracecat_registry.sdk.exceptions import (
    TracecatNotFoundError as SDKNotFoundError,
)
from tracecat_registry.sdk.exceptions import (
    TracecatValidationError as SDKValidationError,
)

from tracecat import config
from tracecat.api.app import app
from tracecat.auth.dependencies import (
    ExecutorWorkspaceRole,
    OrgAdminUser,
    ServiceRole,
    WorkspaceUserRole,
)
from tracecat.auth.types import AccessLevel, Role
from tracecat.cases.durations.schemas import (
    CaseDurationDefinitionCreate,
    CaseDurationEventAnchor,
)
from tracecat.cases.durations.service import CaseDurationDefinitionService
from tracecat.cases.enums import CaseEventType
from tracecat.cases.router import WorkspaceAdminUser, WorkspaceUser
from tracecat.cases.service import CaseFieldsService
from tracecat.contexts import ctx_role
from tracecat.db.dependencies import get_async_session
from tracecat.db.models import User, Workspace

# Advisory lock ID for serializing case_fields schema creation in tests.
# This prevents deadlocks when concurrent tests create workspace-scoped tables
# with FK constraints to the same parent table.
_CASE_FIELDS_SCHEMA_LOCK_ID = 0x7472616365636174  # "tracecat" in hex


@pytest.fixture
async def cases_test_role(svc_workspace: Workspace) -> Role:
    """Create a service role for case UDF tests."""
    return Role(
        type="service",
        access_level=AccessLevel.ADMIN,
        workspace_id=svc_workspace.id,
        user_id=uuid.uuid4(),
        service_id="tracecat-runner",
    )


@pytest.fixture
async def cases_ctx(
    cases_test_role: Role,
    session: AsyncSession,
):
    """Set up the ctx_role and registry context for case UDF tests.

    Also pre-initializes the case_fields workspace schema with an advisory lock
    to prevent deadlocks when multiple tests run concurrently. The deadlock occurs
    because CREATE TABLE with FK constraints acquires ShareRowExclusiveLock on
    the referenced table, and concurrent schema creations can deadlock.

    Uses SDK path with respx mock to route HTTP calls to the FastAPI app.
    """
    # Acquire advisory lock to serialize schema creation across concurrent tests
    await session.execute(
        sa.text(f"SELECT pg_advisory_xact_lock({_CASE_FIELDS_SCHEMA_LOCK_ID})")
    )

    # Pre-initialize the case_fields schema while holding the lock
    fields_service = CaseFieldsService(session=session, role=cases_test_role)
    await fields_service.initialize_workspace_schema()
    await session.commit()

    # Set up registry context for SDK access within UDFs
    registry_ctx = RegistryContext(
        workspace_id=str(cases_test_role.workspace_id),
        workflow_id="test-workflow-id",
        run_id="test-run-id",
        environment="default",
        api_url=config.TRACECAT__API_URL,
    )
    set_context(registry_ctx)

    # Set up respx mock to route SDK HTTP calls to the FastAPI app
    respx_mock = respx.mock(assert_all_mocked=False, assert_all_called=False)
    respx_mock.start()
    respx_mock.route(url__startswith=config.TRACECAT__API_URL).mock(
        side_effect=ASGITransport(app).handle_async_request
    )

    # Override role dependencies to use our test role
    def override_role():
        return cases_test_role

    role_dependencies = [
        ExecutorWorkspaceRole,
        WorkspaceUserRole,
        WorkspaceUser,
        WorkspaceAdminUser,
        ServiceRole,
        OrgAdminUser,
    ]
    for annotated_type in role_dependencies:
        metadata = get_args(annotated_type)
        # metadata[0] is Role, metadata[1] is the Depends(...) object
        if len(metadata) > 1 and hasattr(metadata[1], "dependency"):
            app.dependency_overrides[metadata[1].dependency] = override_role

    # Also need to override the DB session to use the test session
    async def override_get_async_session():
        yield session

    app.dependency_overrides[get_async_session] = override_get_async_session

    token = ctx_role.set(cases_test_role)
    try:
        yield cases_test_role
    finally:
        ctx_role.reset(token)
        clear_context()
        respx_mock.stop()
        app.dependency_overrides.clear()


@pytest.fixture
def blob_storage_config(monkeypatch: pytest.MonkeyPatch):
    """Configure blob storage to use local MinIO for attachment tests.

    The dev stack exposes MinIO at localhost:9000. We need to override the
    config module values to point to this endpoint.
    """
    # Set environment variables for blob storage
    monkeypatch.setenv("TRACECAT__BLOB_STORAGE_ENDPOINT", "http://localhost:9000")
    monkeypatch.setenv("AWS_ACCESS_KEY_ID", "minioadmin")
    monkeypatch.setenv("AWS_SECRET_ACCESS_KEY", "minioadmin")

    # Also update the config module's values directly since they're already loaded
    monkeypatch.setattr(
        config, "TRACECAT__BLOB_STORAGE_ENDPOINT", "http://localhost:9000"
    )

    yield


@pytest.fixture
async def test_user(
    db,  # noqa: ARG001
    session: AsyncSession,
    svc_workspace: Workspace,  # noqa: ARG001
    cases_ctx: Role,  # noqa: ARG001
) -> User:
    """Create a test user for assign_user tests.

    Uses the test session which is shared with the API via dependency override.

    IMPORTANT: This fixture must depend on cases_ctx to ensure the session override
    is in place before the user is created.
    """
    user_id = uuid.uuid4()
    user_email = f"test-user-{uuid.uuid4().hex[:8]}@example.com"

    # Use the test session which is shared with the API
    user = User(
        id=user_id,
        email=user_email,
        hashed_password="hashed",
        first_name="Test",
        last_name="User",
    )
    session.add(user)
    await session.flush()
    return user


# =============================================================================
# create_case characterization tests
# =============================================================================


@pytest.mark.anyio
class TestCreateCase:
    """Characterization tests for create_case UDF."""

    async def test_create_case_basic(self, db, session: AsyncSession, cases_ctx: Role):
        """Create a case with basic required fields."""
        result = await create_case(
            summary="Test Security Alert",
            description="A test security alert for characterization testing.",
        )

        # Validate against SDK type
        TypeAdapter(types.Case).validate_python(result)

        assert result["summary"] == "Test Security Alert"
        assert (
            result["description"]
            == "A test security alert for characterization testing."
        )
        assert "id" in result
        assert "created_at" in result
        assert "updated_at" in result
        assert result["status"] == "unknown"
        assert result["priority"] == "unknown"
        assert result["severity"] == "unknown"

    async def test_create_case_with_all_fields(
        self, db, session: AsyncSession, cases_ctx: Role
    ):
        """Create a case with all optional fields."""
        result = await create_case(
            summary="Critical Security Incident",
            description="A critical security incident requiring immediate attention.",
            priority="critical",
            severity="high",
            status="new",
            payload={"source": "test", "data": [1, 2, 3]},
        )

        assert result["summary"] == "Critical Security Incident"
        assert result["priority"] == "critical"
        assert result["severity"] == "high"
        assert result["status"] == "new"
        assert result["payload"] == {"source": "test", "data": [1, 2, 3]}

    async def test_create_case_with_tags(
        self, db, session: AsyncSession, cases_ctx: Role
    ):
        """Create a case with tags."""
        # First create a tag to use
        tag_name = f"create-case-tag-{uuid.uuid4().hex[:8]}"
        case_for_tag = await create_case(
            summary="Temp Case",
            description="Temporary case to create tag",
        )
        await add_case_tag(
            case_id=str(case_for_tag["id"]),
            tag=tag_name,
            create_if_missing=True,
        )

        # Now create a case with the tag
        result = await create_case(
            summary="Case with Tags",
            description="Test case with tags.",
            tags=[tag_name],
        )

        assert result["summary"] == "Case with Tags"
        assert "id" in result


# =============================================================================
# get_case characterization tests
# =============================================================================


@pytest.mark.anyio
class TestGetCase:
    """Characterization tests for get_case UDF."""

    async def test_get_case_returns_case_details(
        self, db, session: AsyncSession, cases_ctx: Role
    ):
        """Get case returns full case details."""
        created = await create_case(
            summary="Test Case",
            description="Test description",
            priority="medium",
        )

        result = await get_case(case_id=str(created["id"]))

        # Validate against SDK type
        TypeAdapter(types.CaseRead).validate_python(result)

        assert str(result["id"]) == str(created["id"])
        assert result["summary"] == "Test Case"
        assert result["description"] == "Test description"
        assert result["priority"] == "medium"
        assert "fields" in result  # get_case includes field definitions

    async def test_get_case_not_found_raises(
        self, db, session: AsyncSession, cases_ctx: Role
    ):
        """Get case with invalid ID raises SDKNotFoundError."""
        fake_id = str(uuid.uuid4())

        with pytest.raises(SDKNotFoundError):
            await get_case(case_id=fake_id)


# =============================================================================
# update_case characterization tests
# =============================================================================


@pytest.mark.anyio
class TestUpdateCase:
    """Characterization tests for update_case UDF."""

    async def test_update_case_modifies_fields(
        self, db, session: AsyncSession, cases_ctx: Role
    ):
        """Update case modifies specified fields."""
        created = await create_case(
            summary="Original Summary",
            description="Original description",
            priority="low",
        )

        result = await update_case(
            case_id=str(created["id"]),
            summary="Updated Summary",
            priority="high",
        )

        # Validate against SDK type
        TypeAdapter(types.Case).validate_python(result)

        assert result["summary"] == "Updated Summary"
        assert result["priority"] == "high"
        assert result["description"] == "Original description"  # Unchanged

    async def test_update_case_append_description(
        self, db, session: AsyncSession, cases_ctx: Role
    ):
        """Update case with append=True appends to description."""
        created = await create_case(
            summary="Test Case",
            description="Initial description.",
        )

        result = await update_case(
            case_id=str(created["id"]),
            description="Additional notes.",
            append=True,
        )

        assert "Initial description." in result["description"]
        assert "Additional notes." in result["description"]

    async def test_update_case_not_found_raises(
        self, db, session: AsyncSession, cases_ctx: Role
    ):
        """Update case with invalid ID raises ValueError."""
        fake_id = str(uuid.uuid4())

        with pytest.raises(SDKNotFoundError):
            await update_case(case_id=fake_id, summary="New Summary")

    async def test_update_case_severity_and_status(
        self, db, session: AsyncSession, cases_ctx: Role
    ):
        """Update case modifies severity and status."""
        created = await create_case(
            summary="Test Case",
            description="Test description",
            severity="low",
            status="new",
        )

        result = await update_case(
            case_id=str(created["id"]),
            severity="critical",
            status="in_progress",
        )

        assert result["severity"] == "critical"
        assert result["status"] == "in_progress"

    async def test_update_case_payload(
        self, db, session: AsyncSession, cases_ctx: Role
    ):
        """Update case modifies payload."""
        created = await create_case(
            summary="Test Case",
            description="Test description",
            payload={"original": "data"},
        )

        result = await update_case(
            case_id=str(created["id"]),
            payload={"updated": "payload", "new_field": 123},
        )

        assert result["payload"] == {"updated": "payload", "new_field": 123}

    async def test_update_case_tags(self, db, session: AsyncSession, cases_ctx: Role):
        """Update case replaces all tags."""
        created = await create_case(
            summary="Test Case for Tags",
            description="Test description",
        )

        # Add initial tag
        tag1_name = f"tag1-{uuid.uuid4().hex[:8]}"
        await add_case_tag(
            case_id=str(created["id"]),
            tag=tag1_name,
            create_if_missing=True,
        )

        # Create another tag and update case to replace tags
        tag2_name = f"tag2-{uuid.uuid4().hex[:8]}"
        case_for_tag = await create_case(
            summary="Temp",
            description="Temp",
        )
        await add_case_tag(
            case_id=str(case_for_tag["id"]),
            tag=tag2_name,
            create_if_missing=True,
        )

        result = await update_case(
            case_id=str(created["id"]),
            tags=[tag2_name],
        )

        assert "id" in result


# =============================================================================
# list_cases characterization tests
# =============================================================================


@pytest.mark.anyio
class TestListCases:
    """Characterization tests for list_cases UDF."""

    async def test_list_cases_returns_created_cases(
        self, db, session: AsyncSession, cases_ctx: Role
    ):
        """List cases returns cases created in the workspace."""
        await create_case(summary="Case 1", description="Description 1")
        await create_case(summary="Case 2", description="Description 2")

        result = await list_cases()

        TypeAdapter(list[types.CaseReadMinimal]).validate_python(result)
        assert isinstance(result, list)
        assert len(result) >= 2
        summaries = [c["summary"] for c in result]
        assert "Case 1" in summaries
        assert "Case 2" in summaries

    async def test_list_cases_respects_limit(
        self, db, session: AsyncSession, cases_ctx: Role
    ):
        """List cases respects the limit parameter."""
        for i in range(5):
            await create_case(summary=f"Case {i}", description=f"Description {i}")

        result = await list_cases(limit=3)

        assert len(result) == 3

    async def test_list_cases_with_order_by_and_sort(
        self, db, session: AsyncSession, cases_ctx: Role
    ):
        """List cases respects order_by and sort parameters."""
        await create_case(summary="Low Priority", description="Low", priority="low")
        await create_case(summary="High Priority", description="High", priority="high")

        # Order by priority descending
        result = await list_cases(order_by="priority", sort="desc", limit=10)

        assert isinstance(result, list)
        assert len(result) >= 2

    async def test_list_cases_order_by_created_at(
        self, db, session: AsyncSession, cases_ctx: Role
    ):
        """List cases can be ordered by created_at."""
        await create_case(summary="First Case", description="First")
        await create_case(summary="Second Case", description="Second")

        result_asc = await list_cases(order_by="created_at", sort="asc", limit=10)
        result_desc = await list_cases(order_by="created_at", sort="desc", limit=10)

        assert isinstance(result_asc, list)
        assert isinstance(result_desc, list)
        # The order should be different
        if len(result_asc) >= 2 and len(result_desc) >= 2:
            assert result_asc[0]["id"] != result_desc[0]["id"]


# =============================================================================
# search_cases characterization tests
# =============================================================================


@pytest.mark.anyio
class TestSearchCases:
    """Characterization tests for search_cases UDF."""

    async def test_search_cases_by_text(
        self, db, session: AsyncSession, cases_ctx: Role
    ):
        """Search cases finds cases matching search term."""
        await create_case(summary="Security Alert", description="Suspicious activity")
        await create_case(summary="System Update", description="Patch applied")
        await create_case(summary="Security Patch", description="Critical fix")

        result = await search_cases(search_term="Security")

        # Note: search_cases returns Case (from to_dict), different shape than list_cases
        TypeAdapter(list[types.CaseReadMinimal]).validate_python(result)

        assert len(result) >= 2
        summaries = [c["summary"] for c in result]
        assert "Security Alert" in summaries
        assert "Security Patch" in summaries

    async def test_search_cases_by_status(
        self, db, session: AsyncSession, cases_ctx: Role
    ):
        """Search cases filters by status."""
        await create_case(
            summary="Active Case", description="Active", status="in_progress"
        )
        await create_case(summary="Closed Case", description="Closed", status="closed")

        result = await search_cases(status="in_progress")

        assert len(result) >= 1
        assert all(c["status"] == "in_progress" for c in result)

    async def test_search_cases_by_priority(
        self, db, session: AsyncSession, cases_ctx: Role
    ):
        """Search cases filters by priority."""
        await create_case(
            summary="Critical Case", description="Urgent", priority="critical"
        )
        await create_case(summary="Low Case", description="Not urgent", priority="low")

        result = await search_cases(priority="critical")

        assert len(result) >= 1
        assert all(c["priority"] == "critical" for c in result)

    async def test_search_cases_by_severity(
        self, db, session: AsyncSession, cases_ctx: Role
    ):
        """Search cases filters by severity."""
        await create_case(
            summary="High Severity Case", description="Critical", severity="high"
        )
        await create_case(
            summary="Low Severity Case", description="Minor", severity="low"
        )

        result = await search_cases(severity="high")

        assert len(result) >= 1
        assert all(c["severity"] == "high" for c in result)

    async def test_search_cases_with_date_range(
        self, db, session: AsyncSession, cases_ctx: Role
    ):
        """Search cases with date range filters."""
        from datetime import datetime, timedelta

        # Create a case
        await create_case(
            summary="Date Range Test Case",
            description="Testing date filters",
        )

        # Search with start_time from yesterday
        yesterday = datetime.now(UTC) - timedelta(days=1)
        result = await search_cases(start_time=yesterday)

        assert isinstance(result, list)
        # Should include our recently created case
        summaries = [c["summary"] for c in result]
        assert "Date Range Test Case" in summaries

    async def test_search_cases_with_order_and_limit(
        self, db, session: AsyncSession, cases_ctx: Role
    ):
        """Search cases respects order_by, sort, and limit."""
        for i in range(5):
            await create_case(
                summary=f"Ordered Case {i}",
                description=f"Description {i}",
                priority="medium",
            )

        result = await search_cases(
            priority="medium",
            order_by="created_at",
            sort="desc",
            limit=3,
        )

        assert len(result) <= 3

    async def test_search_cases_by_tags(
        self, db, session: AsyncSession, cases_ctx: Role
    ):
        """Search cases filters by tags."""
        case = await create_case(
            summary="Tagged Search Case",
            description="Case with tag for search",
        )
        tag_name = f"search-tag-{uuid.uuid4().hex[:8]}"
        await add_case_tag(
            case_id=str(case["id"]),
            tag=tag_name,
            create_if_missing=True,
        )

        result = await search_cases(tags=[tag_name])

        assert len(result) >= 1
        summaries = [c["summary"] for c in result]
        assert "Tagged Search Case" in summaries


# =============================================================================
# delete_case characterization tests
# =============================================================================


@pytest.mark.anyio
class TestDeleteCase:
    """Characterization tests for delete_case UDF."""

    async def test_delete_case_removes_case(
        self, db, session: AsyncSession, cases_ctx: Role
    ):
        """Delete case removes the case."""
        created = await create_case(
            summary="To Be Deleted",
            description="This case will be deleted",
        )

        await delete_case(case_id=str(created["id"]))

        # Verify case is gone
        with pytest.raises(SDKNotFoundError):
            await get_case(case_id=str(created["id"]))

    async def test_delete_case_not_found_raises(
        self, db, session: AsyncSession, cases_ctx: Role
    ):
        """Delete case with invalid ID raises SDKNotFoundError."""
        fake_id = str(uuid.uuid4())

        with pytest.raises(SDKNotFoundError):
            await delete_case(case_id=fake_id)


# =============================================================================
# create_comment characterization tests
# =============================================================================


@pytest.mark.anyio
class TestCreateComment:
    """Characterization tests for create_comment UDF."""

    async def test_create_comment_adds_comment(
        self, db, session: AsyncSession, cases_ctx: Role
    ):
        """Create comment adds a comment to the case."""
        case = await create_case(
            summary="Case with Comment",
            description="Test case",
        )

        result = await create_comment(
            case_id=str(case["id"]),
            content="This is a test comment.",
        )

        # Validate against SDK type
        TypeAdapter(types.CaseComment).validate_python(result)

        assert result["content"] == "This is a test comment."
        assert "id" in result
        assert "created_at" in result

    async def test_create_comment_case_not_found_raises(
        self, db, session: AsyncSession, cases_ctx: Role
    ):
        """Create comment with invalid case ID raises SDKNotFoundError."""
        fake_id = str(uuid.uuid4())

        with pytest.raises(SDKNotFoundError):
            await create_comment(
                case_id=fake_id,
                content="Test comment",
            )

    async def test_create_comment_with_parent_id(
        self, db, session: AsyncSession, cases_ctx: Role
    ):
        """Create comment as a reply to another comment."""
        case = await create_case(
            summary="Case with Threaded Comments",
            description="Test case",
        )

        # Create parent comment
        parent = await create_comment(
            case_id=str(case["id"]),
            content="Parent comment",
        )

        # Create reply
        reply = await create_comment(
            case_id=str(case["id"]),
            content="Reply to parent",
            parent_id=str(parent["id"]),
        )

        assert reply["content"] == "Reply to parent"
        assert str(reply["parent_id"]) == str(parent["id"])


# =============================================================================
# update_comment characterization tests
# =============================================================================


@pytest.mark.anyio
class TestUpdateComment:
    """Characterization tests for update_comment UDF."""

    async def test_update_comment_modifies_content(
        self, db, session: AsyncSession, cases_ctx: Role
    ):
        """Update comment modifies the comment content."""
        case = await create_case(
            summary="Case with Comment",
            description="Test case",
        )
        comment = await create_comment(
            case_id=str(case["id"]),
            content="Original content",
        )

        result = await update_comment(
            comment_id=str(comment["id"]),
            content="Updated content",
        )

        assert result["content"] == "Updated content"

    async def test_update_comment_not_found_raises(
        self, db, session: AsyncSession, cases_ctx: Role
    ):
        """Update comment with invalid ID raises SDKNotFoundError."""
        fake_id = str(uuid.uuid4())

        with pytest.raises(SDKNotFoundError):
            await update_comment(
                comment_id=fake_id,
                content="Updated content",
            )


# =============================================================================
# list_comments characterization tests
# =============================================================================


@pytest.mark.anyio
class TestListComments:
    """Characterization tests for list_comments UDF."""

    async def test_list_comments_returns_case_comments(
        self, db, session: AsyncSession, cases_ctx: Role
    ):
        """List comments returns all comments for a case."""
        case = await create_case(
            summary="Case with Comments",
            description="Test case",
        )
        await create_comment(case_id=str(case["id"]), content="Comment 1")
        await create_comment(case_id=str(case["id"]), content="Comment 2")

        result = await list_comments(case_id=str(case["id"]))

        # Validate against SDK type
        TypeAdapter(list[types.CaseCommentRead]).validate_python(result)

        assert isinstance(result, list)
        assert len(result) == 2
        contents = [c["content"] for c in result]
        assert "Comment 1" in contents
        assert "Comment 2" in contents

    async def test_list_comments_case_not_found_raises(
        self, db, session: AsyncSession, cases_ctx: Role
    ):
        """List comments with invalid case ID raises SDKNotFoundError."""
        fake_id = str(uuid.uuid4())

        with pytest.raises(SDKNotFoundError):
            await list_comments(case_id=fake_id)


# =============================================================================
# add_case_tag and remove_case_tag characterization tests
# =============================================================================


@pytest.mark.anyio
class TestCaseTags:
    """Characterization tests for case tag UDFs."""

    async def test_add_case_tag_creates_and_adds_tag(
        self, db, session: AsyncSession, cases_ctx: Role
    ):
        """Add case tag with create_if_missing creates the tag."""
        case = await create_case(
            summary="Case with Tag",
            description="Test case",
        )

        result = await add_case_tag(
            case_id=str(case["id"]),
            tag="test-tag-" + uuid.uuid4().hex[:8],
            create_if_missing=True,
        )

        assert "id" in result
        assert "name" in result or "ref" in result

    async def test_add_case_tag_not_found_raises(
        self, db, session: AsyncSession, cases_ctx: Role
    ):
        """Add case tag without create_if_missing raises when tag doesn't exist."""
        case = await create_case(
            summary="Case for Tag Error",
            description="Test case",
        )

        with pytest.raises(SDKNotFoundError):
            await add_case_tag(
                case_id=str(case["id"]),
                tag="nonexistent-tag-" + uuid.uuid4().hex[:8],
                create_if_missing=False,
            )

    async def test_add_case_tag_case_not_found_raises(
        self, db, session: AsyncSession, cases_ctx: Role
    ):
        """Add case tag with invalid case ID raises SDKNotFoundError."""
        fake_id = str(uuid.uuid4())

        with pytest.raises(SDKNotFoundError):
            await add_case_tag(
                case_id=fake_id,
                tag="some-tag",
                create_if_missing=True,
            )

    async def test_remove_case_tag_removes_tag(
        self, db, session: AsyncSession, cases_ctx: Role
    ):
        """Remove case tag removes the tag from the case."""
        case = await create_case(
            summary="Case with Tag",
            description="Test case",
        )
        tag_name = "remove-tag-" + uuid.uuid4().hex[:8]
        await add_case_tag(
            case_id=str(case["id"]),
            tag=tag_name,
            create_if_missing=True,
        )

        # Should not raise
        await remove_case_tag(case_id=str(case["id"]), tag=tag_name)

    async def test_remove_case_tag_case_not_found_raises(
        self, db, session: AsyncSession, cases_ctx: Role
    ):
        """Remove case tag with invalid case ID raises SDKNotFoundError."""
        fake_id = str(uuid.uuid4())

        with pytest.raises(SDKNotFoundError):
            await remove_case_tag(case_id=fake_id, tag="some-tag")


# =============================================================================
# list_case_events characterization tests
# =============================================================================


@pytest.mark.anyio
class TestListCaseEvents:
    """Characterization tests for list_case_events UDF."""

    async def test_list_case_events_returns_events(
        self, db, session: AsyncSession, cases_ctx: Role
    ):
        """List case events returns events for the case."""
        case = await create_case(
            summary="Case with Events",
            description="Test case",
        )

        result = await list_case_events(case_id=str(case["id"]))

        # Validate against SDK type
        TypeAdapter(types.CaseEventsWithUsers).validate_python(result)

        # Should return a dict with events and users lists
        assert isinstance(result, dict)
        assert "events" in result
        assert "users" in result
        assert isinstance(result["events"], list)
        # Creating a case should generate at least one event
        assert len(result["events"]) >= 1

    async def test_list_case_events_not_found_raises(
        self, db, session: AsyncSession, cases_ctx: Role
    ):
        """List case events with invalid case ID raises SDKNotFoundError."""
        fake_id = str(uuid.uuid4())

        with pytest.raises(SDKNotFoundError):
            await list_case_events(case_id=fake_id)

    async def test_list_case_events_invalid_uuid_raises(
        self, db, session: AsyncSession, cases_ctx: Role
    ):
        """List case events with invalid UUID format raises SDKValidationError."""
        with pytest.raises(SDKValidationError):
            await list_case_events(case_id="not-a-uuid")


# =============================================================================
# case task characterization tests
# =============================================================================


@pytest.mark.anyio
class TestCaseTasks:
    """Characterization tests for case task UDFs."""

    async def test_create_task_returns_task(
        self, db, session: AsyncSession, cases_ctx: Role
    ):
        """Create task returns task data."""
        case = await create_case(
            summary="Case with Task",
            description="Test case",
        )

        result = await create_task(
            case_id=str(case["id"]),
            title="Investigate alert",
            description="Check the alert details",
            priority="high",
            status="todo",
        )

        TypeAdapter(types.CaseTaskRead).validate_python(result)

        assert result["title"] == "Investigate alert"
        assert result["priority"] == "high"
        assert result["status"] == "todo"
        assert str(result["case_id"]) == str(case["id"])

    async def test_list_tasks_returns_tasks(
        self, db, session: AsyncSession, cases_ctx: Role
    ):
        """List tasks returns tasks for the case."""
        case = await create_case(
            summary="Case with Multiple Tasks",
            description="Test case",
        )

        await create_task(
            case_id=str(case["id"]),
            title="Triage",
            description="Initial triage",
        )
        await create_task(
            case_id=str(case["id"]),
            title="Containment",
            description="Contain the incident",
        )

        result = await list_tasks(case_id=str(case["id"]))

        TypeAdapter(list[types.CaseTaskRead]).validate_python(result)
        titles = {task["title"] for task in result}
        assert {"Triage", "Containment"}.issubset(titles)


# =============================================================================
# case duration metrics characterization tests
# =============================================================================


@pytest.mark.anyio
class TestCaseDurationMetrics:
    """Characterization tests for case duration metrics UDF."""

    async def test_get_case_metrics_returns_metrics(
        self,
        db,
        session: AsyncSession,
        cases_ctx: Role,
    ):
        """Get case metrics returns duration metrics for the case."""
        definition_payload = CaseDurationDefinitionCreate(
            name=f"Time to Close {uuid.uuid4().hex[:8]}",
            description="Time from case creation to close",
            start_anchor=CaseDurationEventAnchor(
                event_type=CaseEventType.CASE_CREATED,
            ),
            end_anchor=CaseDurationEventAnchor(
                event_type=CaseEventType.CASE_CLOSED,
            ),
        )

        # Use the test session (SDK path)
        definition_service = CaseDurationDefinitionService(
            session=session, role=cases_ctx
        )
        await definition_service.create_definition(definition_payload)

        case = await create_case(
            summary="Case with Duration Metrics",
            description="Test case",
            status="new",
        )
        await update_case(
            case_id=str(case["id"]),
            status="closed",
        )

        result = await get_case_metrics(case_ids=[str(case["id"])])

        TypeAdapter(list[types.CaseDurationMetric]).validate_python(result)
        assert result
        assert result[0]["case_id"] == str(case["id"])


# =============================================================================
# Attachment characterization tests
# =============================================================================


@pytest.mark.anyio
class TestUploadAttachment:
    """Characterization tests for upload_attachment UDF."""

    async def test_upload_attachment_creates_attachment(
        self, db, session: AsyncSession, cases_ctx: Role, blob_storage_config
    ):
        """Upload attachment creates an attachment on the case."""
        case = await create_case(
            summary="Case with Attachment",
            description="Test case",
        )

        # Create simple text content
        content = b"This is test content for attachment."
        content_base64 = base64.b64encode(content).decode("utf-8")

        result = await upload_attachment(
            case_id=str(case["id"]),
            file_name="test-file.txt",
            content_base64=content_base64,
            content_type="text/plain",
        )

        # Validate against SDK type
        TypeAdapter(types.CaseAttachmentRead).validate_python(result)

        assert "id" in result
        assert result["file_name"] == "test-file.txt"
        assert result["content_type"] == "text/plain"
        assert result["size"] == len(content)
        assert "sha256" in result
        assert "created_at" in result

    async def test_upload_attachment_invalid_base64_raises(
        self, db, session: AsyncSession, cases_ctx: Role, blob_storage_config
    ):
        """Upload attachment with invalid base64 raises ValueError."""
        case = await create_case(
            summary="Case for Invalid Upload",
            description="Test case",
        )

        with pytest.raises(SDKValidationError, match="Invalid base64"):
            await upload_attachment(
                case_id=str(case["id"]),
                file_name="test.txt",
                content_base64="not-valid-base64!!!",
                content_type="text/plain",
            )


@pytest.mark.anyio
class TestListAttachments:
    """Characterization tests for list_attachments UDF."""

    async def test_list_attachments_returns_attachments(
        self, db, session: AsyncSession, cases_ctx: Role, blob_storage_config
    ):
        """List attachments returns all attachments for a case."""
        case = await create_case(
            summary="Case with Attachments",
            description="Test case",
        )

        # Upload two attachments
        content1 = base64.b64encode(b"Content 1").decode("utf-8")
        content2 = base64.b64encode(b"Content 2").decode("utf-8")

        await upload_attachment(
            case_id=str(case["id"]),
            file_name="file1.txt",
            content_base64=content1,
            content_type="text/plain",
        )
        await upload_attachment(
            case_id=str(case["id"]),
            file_name="file2.txt",
            content_base64=content2,
            content_type="text/plain",
        )

        result = await list_attachments(case_id=str(case["id"]))

        assert isinstance(result, list)
        assert len(result) == 2
        file_names = [a["file_name"] for a in result]
        assert "file1.txt" in file_names
        assert "file2.txt" in file_names

    async def test_list_attachments_empty_case(
        self, db, session: AsyncSession, cases_ctx: Role, blob_storage_config
    ):
        """List attachments returns empty list for case with no attachments."""
        case = await create_case(
            summary="Case without Attachments",
            description="Test case",
        )

        result = await list_attachments(case_id=str(case["id"]))

        assert isinstance(result, list)
        assert len(result) == 0


@pytest.mark.anyio
class TestGetAttachment:
    """Characterization tests for get_attachment UDF."""

    async def test_get_attachment_returns_metadata(
        self, db, session: AsyncSession, cases_ctx: Role, blob_storage_config
    ):
        """Get attachment returns attachment metadata."""
        case = await create_case(
            summary="Case for Get Attachment",
            description="Test case",
        )

        content = base64.b64encode(b"Test content").decode("utf-8")
        uploaded = await upload_attachment(
            case_id=str(case["id"]),
            file_name="get-test.txt",
            content_base64=content,
            content_type="text/plain",
        )

        result = await get_attachment(
            case_id=str(case["id"]),
            attachment_id=str(uploaded["id"]),
        )

        assert str(result["id"]) == str(uploaded["id"])
        assert result["file_name"] == "get-test.txt"
        assert result["content_type"] == "text/plain"

    async def test_get_attachment_not_found_raises(
        self, db, session: AsyncSession, cases_ctx: Role, blob_storage_config
    ):
        """Get attachment with invalid ID raises SDKNotFoundError."""
        case = await create_case(
            summary="Case for Get Attachment Error",
            description="Test case",
        )
        fake_attachment_id = str(uuid.uuid4())

        with pytest.raises(SDKNotFoundError):
            await get_attachment(
                case_id=str(case["id"]),
                attachment_id=fake_attachment_id,
            )


@pytest.mark.anyio
class TestDownloadAttachment:
    """Characterization tests for download_attachment UDF."""

    async def test_download_attachment_returns_content(
        self, db, session: AsyncSession, cases_ctx: Role, blob_storage_config
    ):
        """Download attachment returns content as base64."""
        case = await create_case(
            summary="Case for Download",
            description="Test case",
        )

        original_content = b"Download test content"
        content_base64 = base64.b64encode(original_content).decode("utf-8")
        uploaded = await upload_attachment(
            case_id=str(case["id"]),
            file_name="download-test.txt",
            content_base64=content_base64,
            content_type="text/plain",
        )

        result = await download_attachment(
            case_id=str(case["id"]),
            attachment_id=str(uploaded["id"]),
        )

        assert "content_base64" in result
        assert result["file_name"] == "download-test.txt"
        assert result["content_type"] == "text/plain"

        # Verify content matches
        downloaded_content = base64.b64decode(result["content_base64"])
        assert downloaded_content == original_content

    async def test_download_attachment_not_found_raises(
        self, db, session: AsyncSession, cases_ctx: Role, blob_storage_config
    ):
        """Download attachment with invalid ID raises SDKNotFoundError."""
        case = await create_case(
            summary="Case for Download Error",
            description="Test case",
        )
        fake_attachment_id = str(uuid.uuid4())

        with pytest.raises(SDKNotFoundError):
            await download_attachment(
                case_id=str(case["id"]),
                attachment_id=fake_attachment_id,
            )


@pytest.mark.anyio
class TestDeleteAttachment:
    """Characterization tests for delete_attachment UDF."""

    async def test_delete_attachment_removes_attachment(
        self, db, session: AsyncSession, cases_ctx: Role, blob_storage_config
    ):
        """Delete attachment removes the attachment from the case."""
        case = await create_case(
            summary="Case for Delete Attachment",
            description="Test case",
        )

        content = base64.b64encode(b"To be deleted").decode("utf-8")
        uploaded = await upload_attachment(
            case_id=str(case["id"]),
            file_name="delete-test.txt",
            content_base64=content,
            content_type="text/plain",
        )

        await delete_attachment(
            case_id=str(case["id"]),
            attachment_id=str(uploaded["id"]),
        )

        # Verify attachment is gone
        with pytest.raises(SDKNotFoundError):
            await get_attachment(
                case_id=str(case["id"]),
                attachment_id=str(uploaded["id"]),
            )

    async def test_delete_attachment_not_found_raises(
        self, db, session: AsyncSession, cases_ctx: Role, blob_storage_config
    ):
        """Delete attachment with invalid ID raises SDKNotFoundError."""
        case = await create_case(
            summary="Case for Delete Error",
            description="Test case",
        )
        fake_attachment_id = str(uuid.uuid4())

        with pytest.raises(SDKNotFoundError):
            await delete_attachment(
                case_id=str(case["id"]),
                attachment_id=fake_attachment_id,
            )


# =============================================================================
# assign_user characterization tests
# =============================================================================


@pytest.mark.anyio
class TestAssignUser:
    """Characterization tests for assign_user UDF."""

    async def test_assign_user_assigns_user_to_case(
        self, db, session: AsyncSession, cases_ctx: Role, test_user: User
    ):
        """Assign user assigns a user to the case."""
        case = await create_case(
            summary="Case for Assignment",
            description="Test case",
        )

        result = await assign_user(
            case_id=str(case["id"]),
            assignee_id=str(test_user.id),
        )

        assert str(result["assignee_id"]) == str(test_user.id)

    async def test_assign_user_case_not_found_raises(
        self, db, session: AsyncSession, cases_ctx: Role, test_user: User
    ):
        """Assign user with invalid case ID raises SDKNotFoundError."""
        fake_id = str(uuid.uuid4())

        with pytest.raises(SDKNotFoundError):
            await assign_user(
                case_id=fake_id,
                assignee_id=str(test_user.id),
            )


# =============================================================================
# assign_user_by_email characterization tests
# =============================================================================


@pytest.mark.anyio
class TestAssignUserByEmail:
    """Characterization tests for assign_user_by_email UDF."""

    async def test_assign_user_by_email_assigns_user(
        self, db, session: AsyncSession, cases_ctx: Role, test_user: User
    ):
        """Assign user by email assigns a user to the case."""
        case = await create_case(
            summary="Case for Email Assignment",
            description="Test case",
        )

        result = await assign_user_by_email(
            case_id=str(case["id"]),
            assignee_email=test_user.email,
        )

        assert str(result["assignee_id"]) == str(test_user.id)

    async def test_assign_user_by_email_user_not_found_raises(
        self, db, session: AsyncSession, cases_ctx: Role
    ):
        """Assign user by email with invalid email raises SDKNotFoundError."""
        case = await create_case(
            summary="Case for Invalid Email",
            description="Test case",
        )

        with pytest.raises(SDKNotFoundError):
            await assign_user_by_email(
                case_id=str(case["id"]),
                assignee_email="nonexistent@example.com",
            )


# =============================================================================
# get_attachment_download_url characterization tests
# =============================================================================


@pytest.mark.anyio
class TestGetAttachmentDownloadUrl:
    """Characterization tests for get_attachment_download_url UDF."""

    async def test_get_attachment_download_url_returns_url(
        self, db, session: AsyncSession, cases_ctx: Role, blob_storage_config
    ):
        """Get attachment download URL returns a presigned URL string."""
        case = await create_case(
            summary="Case for Download URL",
            description="Test case",
        )

        content = base64.b64encode(b"Content for presigned URL").decode("utf-8")
        uploaded = await upload_attachment(
            case_id=str(case["id"]),
            file_name="presigned-test.txt",
            content_base64=content,
            content_type="text/plain",
        )

        result = await get_attachment_download_url(
            case_id=str(case["id"]),
            attachment_id=str(uploaded["id"]),
        )

        assert isinstance(result, str)
        assert result.startswith("http")
        # Presigned URLs contain the bucket and key
        assert "tracecat-attachments" in result or "attachments" in result

    async def test_get_attachment_download_url_with_expiry(
        self, db, session: AsyncSession, cases_ctx: Role, blob_storage_config
    ):
        """Get attachment download URL respects expiry parameter."""
        case = await create_case(
            summary="Case for Download URL Expiry",
            description="Test case",
        )

        content = base64.b64encode(b"Content for expiry test").decode("utf-8")
        uploaded = await upload_attachment(
            case_id=str(case["id"]),
            file_name="expiry-test.txt",
            content_base64=content,
            content_type="text/plain",
        )

        result = await get_attachment_download_url(
            case_id=str(case["id"]),
            attachment_id=str(uploaded["id"]),
            expiry=3600,  # 1 hour
        )

        assert isinstance(result, str)
        assert result.startswith("http")

    async def test_get_attachment_download_url_invalid_expiry_raises(
        self, db, session: AsyncSession, cases_ctx: Role, blob_storage_config
    ):
        """Get attachment download URL with invalid expiry raises ValueError."""
        case = await create_case(
            summary="Case for Invalid Expiry",
            description="Test case",
        )

        content = base64.b64encode(b"Content").decode("utf-8")
        uploaded = await upload_attachment(
            case_id=str(case["id"]),
            file_name="invalid-expiry.txt",
            content_base64=content,
            content_type="text/plain",
        )

        # Negative expiry
        with pytest.raises(SDKValidationError, match="positive"):
            await get_attachment_download_url(
                case_id=str(case["id"]),
                attachment_id=str(uploaded["id"]),
                expiry=-1,
            )

        # Expiry > 24 hours
        with pytest.raises(SDKValidationError, match="24 hours"):
            await get_attachment_download_url(
                case_id=str(case["id"]),
                attachment_id=str(uploaded["id"]),
                expiry=86401,
            )

    async def test_get_attachment_download_url_not_found_raises(
        self, db, session: AsyncSession, cases_ctx: Role, blob_storage_config
    ):
        """Get attachment download URL with invalid attachment ID raises SDKNotFoundError."""
        case = await create_case(
            summary="Case for URL Not Found",
            description="Test case",
        )
        fake_attachment_id = str(uuid.uuid4())

        with pytest.raises(SDKNotFoundError):
            await get_attachment_download_url(
                case_id=str(case["id"]),
                attachment_id=fake_attachment_id,
            )


# =============================================================================
# Edge case tests
# =============================================================================


@pytest.mark.anyio
class TestEdgeCases:
    """Edge case tests for case UDFs."""

    async def test_create_case_invalid_priority_raises(
        self, db, session: AsyncSession, cases_ctx: Role
    ):
        """Create case with invalid priority raises SDKValidationError."""
        with pytest.raises(SDKValidationError):
            await create_case(
                summary="Test Case",
                description="Test",
                priority="invalid_priority",  # type: ignore[arg-type]
            )

    async def test_create_case_invalid_severity_raises(
        self, db, session: AsyncSession, cases_ctx: Role
    ):
        """Create case with invalid severity raises SDKValidationError."""
        with pytest.raises(SDKValidationError):
            await create_case(
                summary="Test Case",
                description="Test",
                severity="invalid_severity",  # type: ignore[arg-type]
            )

    async def test_create_case_invalid_status_raises(
        self, db, session: AsyncSession, cases_ctx: Role
    ):
        """Create case with invalid status raises SDKValidationError."""
        with pytest.raises(SDKValidationError):
            await create_case(
                summary="Test Case",
                description="Test",
                status="invalid_status",  # type: ignore[arg-type]
            )

    async def test_get_case_invalid_uuid_format_raises(
        self, db, session: AsyncSession, cases_ctx: Role
    ):
        """Get case with invalid UUID format raises SDKValidationError."""
        with pytest.raises(SDKValidationError):
            await get_case(case_id="not-a-uuid")

    async def test_assign_user_by_email_case_not_found_raises(
        self, db, session: AsyncSession, cases_ctx: Role
    ):
        """Assign user by email with invalid case ID raises SDKNotFoundError."""
        fake_id = str(uuid.uuid4())

        with pytest.raises(SDKNotFoundError):
            await assign_user_by_email(
                case_id=fake_id,
                assignee_email="test@example.com",
            )

    async def test_upload_attachment_case_not_found_raises(
        self, db, session: AsyncSession, cases_ctx: Role, blob_storage_config
    ):
        """Upload attachment with invalid case ID raises SDKNotFoundError."""
        fake_id = str(uuid.uuid4())
        content = base64.b64encode(b"Test content").decode("utf-8")

        with pytest.raises(SDKNotFoundError):
            await upload_attachment(
                case_id=fake_id,
                file_name="test.txt",
                content_base64=content,
                content_type="text/plain",
            )
