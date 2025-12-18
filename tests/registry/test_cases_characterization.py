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

import pytest
import respx
from httpx import Response
from sqlalchemy.ext.asyncio import AsyncSession
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
    upload_attachment_from_url,
)

from tracecat import config
from tracecat.auth.types import AccessLevel, Role
from tracecat.contexts import ctx_role
from tracecat.db.models import User, Workspace


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
async def cases_ctx(cases_test_role: Role):
    """Set up the ctx_role context for case UDF tests."""
    token = ctx_role.set(cases_test_role)
    try:
        yield cases_test_role
    finally:
        ctx_role.reset(token)


@pytest.fixture
def blob_storage_config(monkeypatch: pytest.MonkeyPatch):
    """Configure blob storage to use local MinIO for attachment tests.

    The dev stack exposes MinIO at localhost:9000. We need to override the
    config module values to point to this endpoint.
    """
    # Set environment variables for blob storage
    monkeypatch.setenv("TRACECAT__BLOB_STORAGE_ENDPOINT", "http://localhost:9000")
    monkeypatch.setenv("MINIO_ROOT_USER", "minio")
    monkeypatch.setenv("MINIO_ROOT_PASSWORD", "password")

    # Also update the config module's values directly since they're already loaded
    monkeypatch.setattr(
        config, "TRACECAT__BLOB_STORAGE_ENDPOINT", "http://localhost:9000"
    )

    yield


@pytest.fixture
async def test_user(db, svc_workspace: Workspace) -> User:
    """Create a test user for assign_user tests.

    We use get_async_session_context_manager() directly to ensure the user
    is visible to CasesService which creates its own session.
    """
    from tracecat.db.engine import get_async_session_context_manager

    user_id = uuid.uuid4()
    user_email = f"test-user-{uuid.uuid4().hex[:8]}@example.com"

    async with get_async_session_context_manager() as session:
        user = User(
            id=user_id,
            email=user_email,
            hashed_password="hashed",  # Not used in these tests
            first_name="Test",
            last_name="User",
        )
        session.add(user)
        await session.commit()
        await session.refresh(user)

    # Return a fresh reference to avoid detached instance issues
    return User(
        id=user_id,
        email=user_email,
        hashed_password="hashed",
        first_name="Test",
        last_name="User",
    )


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

        assert result["id"] == str(created["id"])
        assert result["summary"] == "Test Case"
        assert result["description"] == "Test description"
        assert result["priority"] == "medium"
        assert "fields" in result  # get_case includes field definitions

    async def test_get_case_not_found_raises(
        self, db, session: AsyncSession, cases_ctx: Role
    ):
        """Get case with invalid ID raises ValueError."""
        fake_id = str(uuid.uuid4())

        with pytest.raises(ValueError, match="not found"):
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

        with pytest.raises(ValueError, match="not found"):
            await update_case(case_id=fake_id, summary="New Summary")


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
        with pytest.raises(ValueError, match="not found"):
            await get_case(case_id=str(created["id"]))

    async def test_delete_case_not_found_raises(
        self, db, session: AsyncSession, cases_ctx: Role
    ):
        """Delete case with invalid ID raises ValueError."""
        fake_id = str(uuid.uuid4())

        with pytest.raises(ValueError, match="not found"):
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

        assert result["content"] == "This is a test comment."
        assert "id" in result
        assert "created_at" in result


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

        assert isinstance(result, list)
        assert len(result) == 2
        contents = [c["content"] for c in result]
        assert "Comment 1" in contents
        assert "Comment 2" in contents


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
        """List case events with invalid case ID raises ValueError."""
        fake_id = str(uuid.uuid4())

        with pytest.raises(ValueError, match="not found"):
            await list_case_events(case_id=fake_id)

    async def test_list_case_events_invalid_uuid_raises(
        self, db, session: AsyncSession, cases_ctx: Role
    ):
        """List case events with invalid UUID format raises ValueError."""
        with pytest.raises(ValueError, match="Invalid case ID format"):
            await list_case_events(case_id="not-a-uuid")


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

        with pytest.raises(ValueError, match="Invalid base64"):
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

        assert result["id"] == str(uploaded["id"])
        assert result["file_name"] == "get-test.txt"
        assert result["content_type"] == "text/plain"

    async def test_get_attachment_not_found_raises(
        self, db, session: AsyncSession, cases_ctx: Role, blob_storage_config
    ):
        """Get attachment with invalid ID raises ValueError."""
        case = await create_case(
            summary="Case for Get Attachment Error",
            description="Test case",
        )
        fake_attachment_id = str(uuid.uuid4())

        with pytest.raises(ValueError, match="not found"):
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
        with pytest.raises(ValueError, match="not found"):
            await get_attachment(
                case_id=str(case["id"]),
                attachment_id=str(uploaded["id"]),
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
        """Assign user with invalid case ID raises ValueError."""
        fake_id = str(uuid.uuid4())

        with pytest.raises(ValueError, match="not found"):
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
        """Assign user by email with invalid email raises ValueError."""
        case = await create_case(
            summary="Case for Invalid Email",
            description="Test case",
        )

        with pytest.raises(ValueError, match="not found"):
            await assign_user_by_email(
                case_id=str(case["id"]),
                assignee_email="nonexistent@example.com",
            )


# =============================================================================
# upload_attachment_from_url characterization tests
# =============================================================================


@pytest.mark.anyio
class TestUploadAttachmentFromUrl:
    """Characterization tests for upload_attachment_from_url UDF."""

    @respx.mock
    async def test_upload_attachment_from_url_uploads_content(
        self, db, session: AsyncSession, cases_ctx: Role, blob_storage_config
    ):
        """Upload attachment from URL downloads and uploads content."""
        case = await create_case(
            summary="Case for URL Upload",
            description="Test case",
        )

        # Mock the external URL
        test_content = b"Content downloaded from URL"
        respx.get("https://example.com/test-file.txt").mock(
            return_value=Response(
                200,
                content=test_content,
                headers={"Content-Type": "text/plain"},
            )
        )

        result = await upload_attachment_from_url(
            case_id=str(case["id"]),
            url="https://example.com/test-file.txt",
        )

        assert "id" in result
        assert result["file_name"] == "test-file.txt"
        assert result["content_type"] == "text/plain"
        assert result["size"] == len(test_content)

    @respx.mock
    async def test_upload_attachment_from_url_with_custom_filename(
        self, db, session: AsyncSession, cases_ctx: Role, blob_storage_config
    ):
        """Upload attachment from URL uses custom filename when provided."""
        case = await create_case(
            summary="Case for Custom Filename",
            description="Test case",
        )

        test_content = b"Custom filename content"
        respx.get("https://example.com/some/path").mock(
            return_value=Response(
                200,
                content=test_content,
                headers={"Content-Type": "text/plain"},
            )
        )

        result = await upload_attachment_from_url(
            case_id=str(case["id"]),
            url="https://example.com/some/path",
            file_name="custom-name.txt",
        )

        assert result["file_name"] == "custom-name.txt"


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
        with pytest.raises(ValueError, match="positive"):
            await get_attachment_download_url(
                case_id=str(case["id"]),
                attachment_id=str(uploaded["id"]),
                expiry=-1,
            )

        # Expiry > 24 hours
        with pytest.raises(ValueError, match="24 hours"):
            await get_attachment_download_url(
                case_id=str(case["id"]),
                attachment_id=str(uploaded["id"]),
                expiry=86401,
            )
