import uuid
from collections.abc import AsyncGenerator, Sequence

import pytest
from slugify import slugify
from sqlalchemy.exc import DatabaseError, IntegrityError, NoResultFound
from sqlmodel.ext.asyncio.session import AsyncSession

from tracecat.cases.enums import CasePriority, CaseSeverity, CaseStatus
from tracecat.cases.service import CaseCreate, CasesService
from tracecat.cases.tags.service import CaseTagsService
from tracecat.db.schemas import Case
from tracecat.tags.models import TagCreate
from tracecat.types.auth import Role

pytestmark = pytest.mark.usefixtures("db")


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


@pytest.fixture
async def cases_service(session: AsyncSession, svc_role: Role) -> CasesService:  # noqa: F811
    """Return an instance of CasesService bound to the current session and role."""
    return CasesService(session=session, role=svc_role)


@pytest.fixture
async def case_tags_service(session: AsyncSession, svc_role: Role) -> CaseTagsService:  # noqa: F811
    """Return an instance of CaseTagsService bound to the current session and role."""
    return CaseTagsService(session=session, role=svc_role)


@pytest.fixture
async def case_id(cases_service: CasesService) -> AsyncGenerator[uuid.UUID, None]:
    """Create a temporary case for testing and yield its ID."""
    params = CaseCreate(
        summary="Case w/ Tags",
        description="Integration test case for CaseTagsService",
        status=CaseStatus.NEW,
        priority=CasePriority.MEDIUM,
        severity=CaseSeverity.LOW,
    )
    case: Case = await cases_service.create_case(params)
    try:
        yield case.id
    finally:
        # Clean up case after test
        await cases_service.delete_case(case)


@pytest.fixture
def tag_params() -> TagCreate:
    """Return parameters to create a sample tag."""
    return TagCreate(name="Incident", color="#FFAA00")


@pytest.fixture(
    params=[
        ("Security Incident", "#FF0000", "security-incident"),
        ("High Priority", "#00FF00", "high-priority"),
        ("Network-Issue", "#0000FF", "network-issue"),
        ("Test_Case_123", "#FFFF00", "test-case-123"),
        ("With Spaces And Numbers 123", "#FF00FF", "with-spaces-and-numbers-123"),
    ],
    ids=["security", "priority", "network", "alphanumeric", "complex"],
)
def tag_with_slug(request) -> tuple[TagCreate, str]:
    """Parametrized fixture providing tag creation params and expected slug."""
    name, color, expected_slug = request.param
    return TagCreate(name=name, color=color), expected_slug


@pytest.fixture
async def multiple_tags(
    case_tags_service: CaseTagsService,
) -> AsyncGenerator[list, None]:
    """Create multiple tags for testing."""
    tags = []
    tag_data = [
        ("Critical", "#FF0000"),
        ("Security", "#00FF00"),
        ("Network", "#0000FF"),
        ("Database", "#FFFF00"),
        ("Performance", "#FF00FF"),
    ]

    for name, color in tag_data:
        tag = await case_tags_service.create_tag(TagCreate(name=name, color=color))
        tags.append(tag)

    yield tags

    # Cleanup
    for tag in tags:
        try:
            await case_tags_service.delete_tag(tag)
        except (IntegrityError, DatabaseError, NoResultFound):
            # Expected exceptions during cleanup - tag may be referenced elsewhere
            # or database connection issues. These are acceptable during test cleanup.
            pass


# ---------------------------------------------------------------------------
# Test suite
# ---------------------------------------------------------------------------


class TestCaseTagsService:  # noqa: D101
    @pytest.mark.anyio
    @pytest.mark.parametrize("identifier_attr", ["id", "ref"], ids=["by-id", "by-ref"])
    async def test_add_and_list_tags_for_case(
        self,
        identifier_attr: str,
        case_tags_service: CaseTagsService,
        case_id: uuid.UUID,
        tag_params: TagCreate,
    ) -> None:
        """Add a tag to a case using either its UUID or ref and ensure listing works."""
        # Create a tag first
        tag = await case_tags_service.create_tag(tag_params)

        # Determine which identifier to use when adding the tag
        identifier: str = str(getattr(tag, identifier_attr))

        # Add tag to case
        added_tag = await case_tags_service.add_case_tag(case_id, identifier)
        assert added_tag.id == tag.id
        assert added_tag.name == tag_params.name

        # List tags for the case – should contain exactly one
        case_tags: Sequence = await case_tags_service.list_tags_for_case(case_id)
        assert len(case_tags) == 1
        retrieved = case_tags[0]
        assert retrieved.id == tag.id
        assert retrieved.ref == slugify(tag_params.name)

    @pytest.mark.anyio
    async def test_remove_case_tag(
        self,
        case_tags_service: CaseTagsService,
        case_id: uuid.UUID,
        tag_params: TagCreate,
    ) -> None:
        """Ensure that a tag can be removed from a case via its ref."""
        tag = await case_tags_service.create_tag(tag_params)

        # Attach then detach
        await case_tags_service.add_case_tag(case_id, str(tag.id))
        await case_tags_service.remove_case_tag(case_id, tag.ref)

        # Verify removal
        remaining = await case_tags_service.list_tags_for_case(case_id)
        assert len(remaining) == 0

    @pytest.mark.anyio
    async def test_add_duplicate_tag_idempotent(
        self,
        case_tags_service: CaseTagsService,
        case_id: uuid.UUID,
        tag_params: TagCreate,
    ) -> None:
        """Test that adding the same tag twice is idempotent."""
        tag = await case_tags_service.create_tag(tag_params)

        # Add tag first time
        await case_tags_service.add_case_tag(case_id, str(tag.id))

        # Add same tag again - should not raise error
        await case_tags_service.add_case_tag(case_id, str(tag.id))

        # Verify only one tag association exists
        case_tags = await case_tags_service.list_tags_for_case(case_id)
        assert len(case_tags) == 1
        assert case_tags[0].id == tag.id

    @pytest.mark.anyio
    async def test_add_multiple_tags_to_case(
        self,
        case_tags_service: CaseTagsService,
        case_id: uuid.UUID,
        multiple_tags: list,
    ) -> None:
        """Test adding multiple tags to a single case."""
        # Add all tags to the case
        for tag in multiple_tags:
            await case_tags_service.add_case_tag(case_id, str(tag.id))

        # Verify all tags are associated
        case_tags = await case_tags_service.list_tags_for_case(case_id)
        assert len(case_tags) == len(multiple_tags)

        # Verify all tag IDs match
        case_tag_ids = {tag.id for tag in case_tags}
        expected_ids = {tag.id for tag in multiple_tags}
        assert case_tag_ids == expected_ids

    @pytest.mark.anyio
    @pytest.mark.parametrize("identifier_type", ["id", "ref", "mixed"])
    async def test_remove_tags_by_different_identifiers(
        self,
        identifier_type: str,
        case_tags_service: CaseTagsService,
        case_id: uuid.UUID,
        multiple_tags: list,
    ) -> None:
        """Test removing tags using different identifier types."""
        # Add all tags
        for tag in multiple_tags:
            await case_tags_service.add_case_tag(case_id, str(tag.id))

        # Remove tags based on identifier type
        tags_to_remove = multiple_tags[:3]  # Remove first 3 tags

        for i, tag in enumerate(tags_to_remove):
            if identifier_type == "id":
                identifier = str(tag.id)
            elif identifier_type == "ref":
                identifier = tag.ref
            else:  # mixed
                identifier = str(tag.id) if i % 2 == 0 else tag.ref

            await case_tags_service.remove_case_tag(case_id, identifier)

        # Verify remaining tags
        remaining = await case_tags_service.list_tags_for_case(case_id)
        assert len(remaining) == len(multiple_tags) - len(tags_to_remove)

        remaining_ids = {tag.id for tag in remaining}
        expected_remaining = {tag.id for tag in multiple_tags[3:]}
        assert remaining_ids == expected_remaining

    @pytest.mark.anyio
    async def test_get_case_tag_association(
        self,
        case_tags_service: CaseTagsService,
        case_id: uuid.UUID,
        tag_params: TagCreate,
    ) -> None:
        """Test retrieving a specific case-tag association."""
        tag = await case_tags_service.create_tag(tag_params)

        # Add tag to case
        await case_tags_service.add_case_tag(case_id, str(tag.id))

        # Get the association
        case_tag = await case_tags_service.get_case_tag(case_id, tag.id)
        assert case_tag is not None
        assert case_tag.case_id == case_id
        assert case_tag.tag_id == tag.id

    @pytest.mark.anyio
    async def test_remove_nonexistent_tag_raises_error(
        self,
        case_tags_service: CaseTagsService,
        case_id: uuid.UUID,
        tag_params: TagCreate,
    ) -> None:
        """Test that removing a non-existent tag raises appropriate error."""
        tag = await case_tags_service.create_tag(tag_params)

        # Try to remove tag that was never added
        with pytest.raises(ValueError):
            await case_tags_service.remove_case_tag(case_id, str(tag.id))

    @pytest.mark.anyio
    async def test_tag_slug_generation(
        self,
        case_tags_service: CaseTagsService,
        tag_with_slug: tuple[TagCreate, str],
    ) -> None:
        """Test that tag slugs are generated correctly from various name formats."""
        tag_create, expected_slug = tag_with_slug

        # Create tag
        tag = await case_tags_service.create_tag(tag_create)

        # Verify slug generation
        assert tag.ref == expected_slug
        assert tag.name == tag_create.name

        # Verify we can retrieve by slug
        retrieved = await case_tags_service.get_tag_by_ref(expected_slug)
        assert retrieved.id == tag.id

    @pytest.mark.anyio
    async def test_add_invalid_tag_identifier(
        self,
        case_tags_service: CaseTagsService,
        case_id: uuid.UUID,
    ) -> None:
        """Test that adding a tag with invalid identifier raises appropriate error."""
        # Test with invalid UUID format
        with pytest.raises(NoResultFound):
            await case_tags_service.add_case_tag(case_id, "not-a-uuid-or-valid-ref")

        # Test with non-existent UUID
        non_existent_uuid = uuid.uuid4()
        with pytest.raises(NoResultFound):
            await case_tags_service.add_case_tag(case_id, str(non_existent_uuid))

    @pytest.mark.anyio
    async def test_add_tag_to_nonexistent_case(
        self,
        case_tags_service: CaseTagsService,
        tag_params: TagCreate,
    ) -> None:
        """Test adding a tag to a non-existent case."""
        tag = await case_tags_service.create_tag(tag_params)
        non_existent_case_id = uuid.uuid4()

        # This should fail due to foreign key constraint
        from sqlalchemy.exc import IntegrityError

        with pytest.raises(IntegrityError):
            await case_tags_service.add_case_tag(non_existent_case_id, str(tag.id))
