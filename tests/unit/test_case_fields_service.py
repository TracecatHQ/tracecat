import uuid
from collections.abc import Iterator
from unittest.mock import patch

import pytest
import sqlalchemy as sa
from sqlalchemy.engine.interfaces import ReflectedColumn
from sqlalchemy.ext.asyncio import AsyncSession

from tracecat.auth.types import Role
from tracecat.authz.scopes import SERVICE_PRINCIPAL_SCOPES
from tracecat.cases.enums import (
    CaseFieldReadType,
    CasePriority,
    CaseSeverity,
    CaseStatus,
)
from tracecat.cases.schemas import (
    CaseFieldCreate,
    CaseFieldReadMinimal,
    CaseFieldUpdate,
)
from tracecat.cases.service import CaseFieldsService
from tracecat.db.models import Case, CaseFields
from tracecat.exceptions import TracecatAuthorizationError
from tracecat.tables.enums import SqlType

pytestmark = pytest.mark.usefixtures("db")


@pytest.fixture(scope="session", autouse=True)
def workflow_bucket() -> Iterator[None]:
    """Disable MinIO-dependent workflow bucket setup for these tests."""
    yield


@pytest.fixture
async def case_fields_service(
    session: AsyncSession, svc_role: Role
) -> CaseFieldsService:
    """Create a case fields service instance for testing."""
    return CaseFieldsService(session=session, role=svc_role)


@pytest.fixture
async def test_case(session: AsyncSession, svc_role: Role) -> Case:
    """Create a test case for use in field tests."""
    # Create a case directly using SQLAlchemy
    case = Case(
        workspace_id=svc_role.workspace_id if svc_role.workspace_id else uuid.uuid4(),
        case_number=1,
        summary="Test Case for Fields",
        description="This is a test case for testing fields",
        status=CaseStatus.NEW,
        priority=CasePriority.MEDIUM,
        severity=CaseSeverity.LOW,
    )
    session.add(case)
    await session.commit()
    await session.refresh(case)
    return case


@pytest.mark.anyio
class TestCaseFieldsService:
    async def test_init_requires_workspace_id(self, session: AsyncSession) -> None:
        """Test that service initialization requires a workspace ID."""
        # Create a role without workspace_id
        role_without_workspace = Role(
            type="service",
            user_id=uuid.uuid4(),
            workspace_id=None,
            service_id="tracecat-service",
            scopes=SERVICE_PRINCIPAL_SCOPES["tracecat-service"],
        )

        # Attempt to create service without workspace should raise error
        with pytest.raises(TracecatAuthorizationError):
            CaseFieldsService(session=session, role=role_without_workspace)

    async def test_list_fields(self, case_fields_service: CaseFieldsService) -> None:
        """Test listing case fields."""
        # Mock the editor.get_columns method
        mock_columns = [
            {"name": "id", "type": sa.types.UUID(), "nullable": False},
            {"name": "case_id", "type": sa.types.UUID(), "nullable": False},
            {"name": "created_at", "type": sa.types.TIMESTAMP(), "nullable": False},
            {"name": "updated_at", "type": sa.types.TIMESTAMP(), "nullable": False},
            {"name": "custom_field1", "type": sa.types.String(), "nullable": True},
            {"name": "custom_field2", "type": sa.types.Integer(), "nullable": True},
        ]

        with patch.object(
            case_fields_service.editor, "get_columns"
        ) as mock_get_columns:
            mock_get_columns.return_value = mock_columns

            # Call the list_fields method
            fields = await case_fields_service.list_fields()

            # Verify list_fields delegates the visible-column contract to the editor
            assert fields == mock_columns
            mock_get_columns.assert_called_once()

    async def test_case_field_read_accepts_timestamptz_type(self) -> None:
        """Ensure TIMESTAMPTZ columns can be read for custom fields."""
        column = ReflectedColumn(
            name="last_seen_at",
            type=sa.types.TIMESTAMP(timezone=True),
            nullable=False,
            default=None,
            comment=None,
        )

        field = CaseFieldReadMinimal.from_sa(column)
        assert field.type is CaseFieldReadType.TIMESTAMPTZ
        assert field.reserved is False

    async def test_case_field_read_normalises_timestamptz_string(self) -> None:
        """Ensure reflected TIMESTAMP WITH TIME ZONE strings are supported."""
        column = ReflectedColumn(
            name="custom_tz_field",
            type="TIMESTAMP WITH TIME ZONE",  # pyright: ignore[reportArgumentType]
            nullable=True,
            default=None,
            comment=None,
        )

        field = CaseFieldReadMinimal.from_sa(column)
        assert field.type is CaseFieldReadType.TIMESTAMPTZ
        assert field.reserved is False

    async def test_case_field_read_preserves_reserved_uuid_type(self) -> None:
        """Ensure reserved UUID columns remain UUID in read metadata."""
        column = ReflectedColumn(
            name="case_id",
            type=sa.types.UUID(),
            nullable=False,
            default=None,
            comment=None,
        )

        field = CaseFieldReadMinimal.from_sa(column)
        assert field.type is CaseFieldReadType.UUID
        assert field.reserved is True

    async def test_case_field_read_prefers_schema_type_metadata(self) -> None:
        """Ensure user-defined field metadata drives the public read type."""
        column = ReflectedColumn(
            name="severity_band",
            type=sa.types.String(),
            nullable=True,
            default=None,
            comment=None,
        )

        field = CaseFieldReadMinimal.from_sa(
            column,
            field_schema={
                "severity_band": {
                    "type": "SELECT",
                    "options": ["low", "high"],
                }
            },
        )
        assert field.type is CaseFieldReadType.SELECT
        assert field.options == ["low", "high"]

    async def test_create_field(self, case_fields_service: CaseFieldsService) -> None:
        """Test creating a case field."""
        # Create field parameters
        field_params = CaseFieldCreate(
            name="test_field",
            type=SqlType.TEXT,
        )

        # Mock the editor.create_column method
        with patch.object(
            case_fields_service.editor, "create_column"
        ) as mock_create_column:
            # Call the create_field method
            await case_fields_service.create_field(field_params)

            # Verify the method was called with the right parameters
            mock_create_column.assert_called_once_with(field_params)

    async def test_create_field_rejects_internal_name(
        self, case_fields_service: CaseFieldsService
    ) -> None:
        """Internal/system-managed column names should be rejected."""
        with pytest.raises(ValueError, match="reserved for internal use"):
            await case_fields_service.create_field(
                CaseFieldCreate(name="__tc_workspace_id", type=SqlType.TEXT)
            )

        with pytest.raises(ValueError, match="reserved for internal use"):
            await case_fields_service.create_field(
                CaseFieldCreate(name="__TC_workspace_id", type=SqlType.TEXT)
            )

    async def test_create_field_rejects_internal_namespace_prefix(
        self, case_fields_service: CaseFieldsService
    ) -> None:
        """The internal Tracecat namespace prefix should be reserved."""
        with pytest.raises(ValueError, match="reserved for internal use"):
            await case_fields_service.create_field(
                CaseFieldCreate(name="__tc_shadow", type=SqlType.TEXT)
            )

    async def test_update_field(self, case_fields_service: CaseFieldsService) -> None:
        """Test updating a case field."""
        # Create field update parameters
        field_update = CaseFieldUpdate(
            name="updated_field",
        )

        # Mock the editor.update_column method
        with patch.object(
            case_fields_service.editor, "update_column"
        ) as mock_update_column:
            # Call the update_field method
            await case_fields_service.update_field("test_field", field_update)

            # Verify the method was called with the right parameters
            mock_update_column.assert_called_once_with("test_field", field_update)

    async def test_update_field_required_on_closure_without_schema_entry(
        self, case_fields_service: CaseFieldsService
    ) -> None:
        """required_on_closure must persist even when the field has no schema entry.

        Regression: when a field existed physically but had no entry in the
        CaseFields.schema JSONB, update_field silently skipped the schema
        write because field_type resolved to None.
        """
        reflected_column: ReflectedColumn = {
            "name": "my_field",
            "type": sa.Text(),
            "nullable": True,
            "default": None,
            "comment": None,
            "autoincrement": False,
        }

        with (
            patch.object(
                case_fields_service,
                "get_field_schema",
                return_value={},
            ),
            patch.object(
                case_fields_service.editor,
                "update_column",
            ),
            patch.object(
                case_fields_service.editor,
                "get_columns",
                return_value=[reflected_column],
            ),
            patch.object(
                case_fields_service,
                "_update_field_schema",
            ) as mock_update_schema,
            patch.object(case_fields_service.session, "commit"),
        ):
            await case_fields_service.update_field(
                "my_field",
                CaseFieldUpdate(required_on_closure=True),
            )

            mock_update_schema.assert_called_once_with(
                "my_field",
                {"type": "TEXT", "required_on_closure": True},
            )

    async def test_update_field_rejects_internal_name(
        self, case_fields_service: CaseFieldsService
    ) -> None:
        """Updating an internal/system-managed column should be rejected."""
        with pytest.raises(ValueError, match="reserved for internal use"):
            await case_fields_service.update_field(
                "__tc_workspace_id", CaseFieldUpdate(name="updated_field")
            )

    async def test_update_field_rejects_rename_to_internal_name(
        self, case_fields_service: CaseFieldsService
    ) -> None:
        """Renaming a field to an internal/system-managed name should be rejected."""
        with pytest.raises(ValueError, match="reserved for internal use"):
            await case_fields_service.update_field(
                "test_field", CaseFieldUpdate(name="__tc_workspace_id")
            )

    async def test_delete_field(self, case_fields_service: CaseFieldsService) -> None:
        """Test deleting a case field."""
        # Mock the editor.delete_column method
        with patch.object(
            case_fields_service.editor, "delete_column"
        ) as mock_delete_column:
            # Call the delete_field method
            await case_fields_service.delete_field("test_field")

            # Verify the method was called with the right parameters
            mock_delete_column.assert_called_once_with("test_field")

    async def test_delete_field_reserved(
        self, case_fields_service: CaseFieldsService
    ) -> None:
        """Test that deleting a reserved field raises an error."""
        # Try to delete a reserved field (using a field from the CaseFields model)
        with pytest.raises(ValueError, match="Field case_id is a reserved field"):
            await case_fields_service.delete_field("case_id")

    async def test_get_fields_none(
        self, case_fields_service: CaseFieldsService, test_case: Case
    ) -> None:
        """Test getting fields when case has no row in the workspace table."""
        # Initialize the schema but don't create any rows
        await case_fields_service.initialize_workspace_schema()

        # Get fields for the case - should return None since no row exists
        fields = await case_fields_service.get_fields(test_case)

        # Verify no fields were returned
        assert fields is None

    async def test_get_fields(
        self,
        case_fields_service: CaseFieldsService,
        test_case: Case,
        session: AsyncSession,
    ) -> None:
        """Test getting fields for a case with field values."""
        # Initialize workspace schema and ensure a row exists for the case
        await case_fields_service.initialize_workspace_schema()
        row_id = await case_fields_service.ensure_workspace_row(test_case.id)

        # Mock the editor.get_row method to return a response
        mock_fields_data = {
            "id": row_id,
            "case_id": test_case.id,
            "custom_field1": "test value",
            "custom_field2": 123,
        }

        with patch.object(case_fields_service.editor, "get_row") as mock_get_row:
            mock_get_row.return_value = mock_fields_data

            # Get fields for the case
            fields = await case_fields_service.get_fields(test_case)

            # Verify the fields were returned correctly
            assert fields == mock_fields_data
            mock_get_row.assert_called_once_with(row_id)

    async def test_batch_get_fields_selects_only_requested_columns(
        self,
        case_fields_service: CaseFieldsService,
        test_case: Case,
    ) -> None:
        """Batch hydration should return only explicitly requested field IDs."""
        await case_fields_service.create_field(
            CaseFieldCreate(name="custom_field1", type=SqlType.TEXT)
        )
        await case_fields_service.create_field(
            CaseFieldCreate(name="custom_field2", type=SqlType.INTEGER)
        )
        await case_fields_service.upsert_field_values(
            test_case,
            {"custom_field1": "alpha", "custom_field2": 123},
        )

        fields_by_case = await case_fields_service.batch_get_fields(
            [test_case.id], ["custom_field2"]
        )

        assert fields_by_case == {test_case.id: {"custom_field2": 123}}

    async def test_batch_get_fields_uses_reflected_columns_when_schema_missing(
        self,
        case_fields_service: CaseFieldsService,
        test_case: Case,
    ) -> None:
        """Reflected columns should keep hydration working when schema metadata is stale."""
        await case_fields_service.create_field(
            CaseFieldCreate(name="custom_field1", type=SqlType.TEXT)
        )
        await case_fields_service.create_field(
            CaseFieldCreate(name="custom_field2", type=SqlType.INTEGER)
        )
        await case_fields_service.upsert_field_values(
            test_case,
            {"custom_field1": "alpha", "custom_field2": 123},
        )

        reflected_columns = [
            {
                "name": "custom_field1",
                "type": sa.types.String(),
                "nullable": True,
                "default": None,
                "comment": None,
            },
            {
                "name": "custom_field2",
                "type": sa.types.Integer(),
                "nullable": True,
                "default": None,
                "comment": None,
            },
        ]

        with (
            patch.object(case_fields_service, "get_field_schema", return_value={}),
            patch.object(
                case_fields_service.editor,
                "get_columns",
                return_value=reflected_columns,
            ),
        ):
            fields_by_case = await case_fields_service.batch_get_fields(
                [test_case.id], ["custom_field2"]
            )

        assert fields_by_case == {test_case.id: {"custom_field2": 123}}

    async def test_batch_get_fields_rejects_reserved_field_ids(
        self,
        case_fields_service: CaseFieldsService,
        test_case: Case,
    ) -> None:
        """Reserved case-field columns should not be selectable for hydration."""
        with pytest.raises(ValueError, match="reserved field"):
            await case_fields_service.batch_get_fields([test_case.id], ["case_id"])

        with pytest.raises(ValueError, match="reserved field"):
            await case_fields_service.batch_get_fields([test_case.id], ["CASE_ID"])

    async def test_batch_get_fields_rejects_internal_field_ids(
        self,
        case_fields_service: CaseFieldsService,
        test_case: Case,
    ) -> None:
        """Internal/system-managed field IDs should be rejected."""
        with pytest.raises(ValueError, match="reserved for internal use"):
            await case_fields_service.batch_get_fields(
                [test_case.id], ["__tc_workspace_id"]
            )

    async def test_upsert_field_values(
        self, case_fields_service: CaseFieldsService, test_case: Case
    ) -> None:
        """Test upserting field values for a case."""
        # Field values to upsert
        fields_data = {"custom_field1": "test value", "custom_field2": 123}

        # Mock result from editor.update_row
        mock_result = {"id": uuid.uuid4(), "case_id": test_case.id, **fields_data}

        # Mock the editor methods
        with (
            patch.object(
                case_fields_service, "ensure_workspace_row"
            ) as mock_ensure_row,
            patch.object(case_fields_service.editor, "update_row") as mock_update_row,
        ):
            mock_ensure_row.return_value = uuid.uuid4()
            mock_update_row.return_value = mock_result

            # Upsert field values
            result = await case_fields_service.upsert_field_values(
                test_case, fields_data
            )

            # Verify the result matches the full mock_result
            assert result == mock_result
            mock_ensure_row.assert_called_once_with(test_case.id)
            mock_update_row.assert_called_once()

            # Verify the call arguments
            call_kwargs = mock_update_row.call_args.kwargs
            assert "row_id" in call_kwargs
            assert "data" in call_kwargs
            assert call_kwargs["data"] == fields_data

    async def test_upsert_field_values_empty_fields(
        self, case_fields_service: CaseFieldsService, test_case: Case
    ) -> None:
        """Test upserting with empty fields returns row without updates."""
        # Mock the editor methods
        mock_row_id = uuid.uuid4()
        mock_row = {"id": mock_row_id, "case_id": test_case.id}

        with (
            patch.object(
                case_fields_service, "ensure_workspace_row"
            ) as mock_ensure_row,
            patch.object(case_fields_service.editor, "get_row") as mock_get_row,
        ):
            mock_ensure_row.return_value = mock_row_id
            mock_get_row.return_value = mock_row

            # Upsert with empty fields
            result = await case_fields_service.upsert_field_values(test_case, {})

            # Should call get_row instead of update_row when fields is empty
            assert result == mock_row
            mock_ensure_row.assert_called_once_with(test_case.id)
            mock_get_row.assert_called_once_with(row_id=mock_row_id)

    async def test_upsert_field_values_rejects_internal_column(
        self, case_fields_service: CaseFieldsService, test_case: Case
    ) -> None:
        """Internal/system-managed field values should be rejected."""
        with (
            pytest.raises(ValueError, match="reserved for internal use"),
            patch.object(
                case_fields_service, "ensure_workspace_row"
            ) as mock_ensure_workspace_row,
        ):
            await case_fields_service.upsert_field_values(
                test_case, {"__tc_workspace_id": str(uuid.uuid4())}
            )

        mock_ensure_workspace_row.assert_not_called()

    async def test_ensure_workspace_row_reuses_existing_row_on_case_conflict(
        self,
        case_fields_service: CaseFieldsService,
        test_case: Case,
        session: AsyncSession,
    ) -> None:
        """Ensure conflict on case_id reuses the existing workspace row."""
        await case_fields_service.initialize_workspace_schema()

        # Build a SQLAlchemy Table object matching the workspace table structure
        workspace_table = sa.Table(
            case_fields_service.sanitized_table_name,
            sa.MetaData(),
            sa.Column("id", sa.UUID, primary_key=True),
            sa.Column("case_id", sa.UUID, nullable=False),
            schema=case_fields_service.schema_name,
        )

        # Seed workspace table with an existing row for the case_id
        existing_row_id = uuid.uuid4()
        insert_stmt = sa.insert(workspace_table).values(
            id=existing_row_id, case_id=test_case.id
        )
        await session.execute(insert_stmt)

        # Call _ensure_workspace_row - should return the existing row id
        returned_id = await case_fields_service.ensure_workspace_row(test_case.id)

        # Verify the returned ID is the existing row's ID
        assert returned_id == existing_row_id

        # Verify the workspace row keeps its original id
        select_stmt = sa.select(workspace_table.c.id, workspace_table.c.case_id).where(
            workspace_table.c.case_id == test_case.id
        )
        result = await session.execute(select_stmt)
        row = result.one()
        assert row.id == existing_row_id
        assert row.case_id == test_case.id

    async def test_ensure_schema_ready_creates_if_missing(
        self, case_fields_service: CaseFieldsService, session: AsyncSession
    ) -> None:
        """Test that _ensure_schema_ready creates schema if it doesn't exist."""
        # Ensure schema doesn't exist initially
        assert case_fields_service._schema_initialized is False

        # Call _ensure_schema_ready
        await case_fields_service._ensure_schema_ready()

        # Verify schema is now initialized
        assert case_fields_service._schema_initialized is True

        # Verify the actual schema and table exist
        conn = await session.connection()

        def check_exists(sync_conn: sa.Connection) -> bool:
            inspector = sa.inspect(sync_conn)
            return inspector.has_schema(
                case_fields_service.schema_name
            ) and inspector.has_table(
                case_fields_service.sanitized_table_name,
                schema=case_fields_service.schema_name,
            )

        exists = await conn.run_sync(check_exists)
        assert exists is True

    async def test_delete_all_reserved_fields_raises(
        self, case_fields_service: CaseFieldsService
    ) -> None:
        """Test that all reserved fields cannot be deleted."""
        for reserved_field in case_fields_service._reserved_columns:
            with pytest.raises(
                ValueError, match=f"Field {reserved_field} is a reserved"
            ):
                await case_fields_service.delete_field(reserved_field)

    async def test_delete_field_removes_from_schema(
        self, case_fields_service: CaseFieldsService, session: AsyncSession
    ) -> None:
        """Test that delete_field removes the field from schema."""
        # Initialize workspace and create definition with schema
        await case_fields_service.initialize_workspace_schema()
        definition = CaseFields(
            workspace_id=case_fields_service.workspace_id,
            schema={
                "field_to_delete": {"type": "TEXT"},
                "field_to_keep": {"type": "INTEGER"},
            },
        )
        session.add(definition)
        await session.flush()

        # Delete the field
        with patch.object(
            case_fields_service.editor, "delete_column"
        ) as mock_delete_column:
            await case_fields_service.delete_field("field_to_delete")
            mock_delete_column.assert_called_once_with("field_to_delete")

        # Verify schema was updated
        await session.refresh(definition)
        assert "field_to_delete" not in definition.schema
        assert "field_to_keep" in definition.schema
