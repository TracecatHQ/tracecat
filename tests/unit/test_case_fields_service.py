import uuid
from collections.abc import Iterator
from decimal import Decimal
from typing import Any
from unittest.mock import patch

import orjson
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
from tracecat.db.models import Case, CaseFields, WorkspaceSyncResourceMapping
from tracecat.exceptions import TracecatAuthorizationError
from tracecat.tables.common import render_default_value
from tracecat.tables.enums import SqlType
from tracecat.workspace_sync.enums import SyncResourceType, VcsProvider

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

    @pytest.mark.parametrize(
        ("name", "column_type", "field_schema", "expected_type", "reserved"),
        [
            pytest.param(
                "last_seen_at",
                sa.types.TIMESTAMP(timezone=True),
                None,
                CaseFieldReadType.TIMESTAMPTZ,
                False,
                id="timestamptz-type",
            ),
            pytest.param(
                "custom_tz_field",
                "TIMESTAMP WITH TIME ZONE",
                None,
                CaseFieldReadType.TIMESTAMPTZ,
                False,
                id="timestamptz-string",
            ),
            pytest.param(
                "case_id",
                sa.types.UUID(),
                None,
                CaseFieldReadType.UUID,
                True,
                id="reserved-uuid",
            ),
            pytest.param(
                "severity_band",
                sa.types.String(),
                {"severity_band": {"type": "SELECT", "options": ["low", "high"]}},
                CaseFieldReadType.SELECT,
                False,
                id="schema-type-metadata",
            ),
        ],
    )
    async def test_case_field_read_type(
        self,
        name: str,
        column_type: Any,
        field_schema: dict[str, Any] | None,
        expected_type: CaseFieldReadType,
        reserved: bool,
    ) -> None:
        column = ReflectedColumn(
            name=name,
            type=column_type,
            nullable=True,
            default=None,
            comment=None,
        )

        field = CaseFieldReadMinimal.from_sa(column, field_schema=field_schema)
        assert field.type is expected_type
        assert field.reserved is reserved
        if field_schema is not None:
            assert field.options == ["low", "high"]

    @pytest.mark.parametrize(
        ("field_id", "field_type", "field_schema", "expected_display_name"),
        [
            (
                "analyst_verdict",
                sa.types.Text(),
                {
                    "analyst_verdict": {
                        "type": "TEXT",
                        "display_name": "Analyst Verdict",
                    }
                },
                "Analyst Verdict",
            ),
            (
                "analyst_verdict",
                sa.types.Text(),
                {"analyst_verdict": {"type": "TEXT"}},
                "analyst_verdict",
            ),
            ("analyst_verdict", sa.types.Text(), None, "analyst_verdict"),
            ("case_id", sa.types.UUID(), None, "case_id"),
        ],
    )
    async def test_case_field_read_display_name(
        self,
        field_id: str,
        field_type: Any,
        field_schema: dict[str, Any] | None,
        expected_display_name: str,
    ) -> None:
        """Read display names from metadata and fall back to the field ID."""
        column = ReflectedColumn(
            name=field_id,
            type=field_type,
            nullable=True,
            default=None,
            comment=None,
        )

        field = CaseFieldReadMinimal.from_sa(column, field_schema=field_schema)

        assert field.id == field_id
        assert field.display_name == expected_display_name

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

    async def test_write_field_display_name_metadata(
        self,
        case_fields_service: CaseFieldsService,
    ) -> None:
        """Write, preserve, and backfill field display-name metadata."""
        with (
            patch.object(case_fields_service.editor, "create_column"),
            patch.object(case_fields_service.editor, "update_column"),
        ):
            await case_fields_service.create_field(
                CaseFieldCreate(
                    name="analyst_verdict",
                    display_name="Analyst Verdict",
                    type=SqlType.TEXT,
                )
            )
            schema = await case_fields_service.get_field_schema()
            assert schema["analyst_verdict"]["display_name"] == "Analyst Verdict"

            await case_fields_service.create_field(
                CaseFieldCreate(name="legacy_field", type=SqlType.TEXT)
            )
            schema = await case_fields_service.get_field_schema()
            assert schema["legacy_field"]["display_name"] == "legacy_field"

            await case_fields_service.update_field(
                "analyst_verdict",
                CaseFieldUpdate(display_name="Analyst decision"),
            )
            schema = await case_fields_service.get_field_schema()
            assert schema["analyst_verdict"]["display_name"] == "Analyst decision"

            await case_fields_service.update_field(
                "analyst_verdict",
                CaseFieldUpdate(nullable=True),
            )
            schema = await case_fields_service.get_field_schema()
            assert schema["analyst_verdict"]["display_name"] == "Analyst decision"

            await case_fields_service._update_field_schema(
                "legacy_field",
                {"type": "TEXT"},
            )
            await case_fields_service.update_field(
                "legacy_field",
                CaseFieldUpdate(nullable=True),
            )
            schema = await case_fields_service.get_field_schema()
            assert schema["legacy_field"]["display_name"] == "legacy_field"

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

    async def test_update_field_rename_moves_workspace_sync_mapping(
        self,
        session: AsyncSession,
        svc_role: Role,
        case_fields_service: CaseFieldsService,
    ) -> None:
        """Renaming a synced case field must keep its Git source identity."""
        definition = CaseFields(
            workspace_id=svc_role.workspace_id,
            schema={"external_ref": {"type": "TEXT"}},
        )
        session.add(definition)
        await session.flush()
        old_local_id = uuid.uuid5(definition.id, "external_ref")
        github_mapping = WorkspaceSyncResourceMapping(
            workspace_id=svc_role.workspace_id,
            provider=VcsProvider.GITHUB.value,
            resource_type=SyncResourceType.CASE_FIELD.value,
            source_id="external_ref",
            source_path="case_fields/external_ref.yml",
            local_id=old_local_id,
        )
        gitlab_mapping = WorkspaceSyncResourceMapping(
            workspace_id=svc_role.workspace_id,
            provider=VcsProvider.GITLAB.value,
            resource_type=SyncResourceType.CASE_FIELD.value,
            source_id="external_ref",
            source_path="case_fields/external_ref.yml",
            local_id=old_local_id,
        )
        session.add_all([github_mapping, gitlab_mapping])
        await session.flush()

        with patch.object(case_fields_service.editor, "update_column"):
            await case_fields_service.update_field(
                "external_ref",
                CaseFieldUpdate(name="vendor_ticket_id"),
            )

        new_local_id = uuid.uuid5(definition.id, "vendor_ticket_id")
        await session.refresh(github_mapping)
        await session.refresh(gitlab_mapping)
        assert github_mapping.source_id == "external_ref"
        assert github_mapping.source_path == "case_fields/external_ref.yml"
        assert github_mapping.local_id == new_local_id
        assert gitlab_mapping.source_id == "external_ref"
        assert gitlab_mapping.source_path == "case_fields/external_ref.yml"
        assert gitlab_mapping.local_id == new_local_id

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
                {
                    "type": "TEXT",
                    "display_name": "my_field",
                    "required_on_closure": True,
                },
            )

    async def test_update_field_multi_select_default_without_type(
        self, case_fields_service: CaseFieldsService
    ) -> None:
        """Updating a MULTI_SELECT default without resending the type must render JSON."""
        await case_fields_service.create_field(
            CaseFieldCreate(
                name="multi_default_field",
                type=SqlType.MULTI_SELECT,
                options=["alpha", "beta", "gamma"],
                default=["beta"],
            )
        )

        await case_fields_service.update_field(
            "multi_default_field",
            CaseFieldUpdate(default=["beta", "gamma"]),
        )

        columns = await case_fields_service.editor.get_columns()
        column = next(c for c in columns if c["name"] == "multi_default_field")
        default_sql = column["default"]
        assert default_sql is not None
        assert '["beta", "gamma"]' in default_sql or '["beta","gamma"]' in default_sql

    async def test_create_field_jsonb_default_with_escaped_quotes(
        self, case_fields_service: CaseFieldsService
    ) -> None:
        """JSONB defaults containing escaped quotes must produce valid DDL."""
        await case_fields_service.create_field(
            CaseFieldCreate(
                name="jsonb_default_field",
                type=SqlType.JSONB,
                default={"note": 'say "hi"', "path": "C:\\tmp"},
            )
        )

        columns = await case_fields_service.editor.get_columns()
        column = next(c for c in columns if c["name"] == "jsonb_default_field")
        assert column["default"] is not None

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

    async def test_update_field_rejects_sql_injection_like_field_id(
        self,
        case_fields_service: CaseFieldsService,
    ) -> None:
        """Reject field IDs that are not valid SQL identifiers before DDL runs."""
        await case_fields_service.create_field(
            CaseFieldCreate(
                name="safe_field",
                type=SqlType.TEXT,
            )
        )

        with pytest.raises(ValueError, match="Identifier must"):
            await case_fields_service.update_field(
                'safe_field"; DROP TABLE case_fields; --',
                CaseFieldUpdate(default="still text"),
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

    @pytest.mark.parametrize("with_cases", [True, False])
    @pytest.mark.parametrize(
        ("field_id", "error"),
        [
            ("case_id", "reserved field"),
            ("CASE_ID", "reserved field"),
            ("__tc_workspace_id", "reserved for internal use"),
        ],
    )
    async def test_batch_get_fields_rejects_reserved_and_internal_field_ids(
        self,
        case_fields_service: CaseFieldsService,
        test_case: Case,
        with_cases: bool,
        field_id: str,
        error: str,
    ) -> None:
        """Reserved and internal columns are rejected, even for empty result pages."""
        case_ids = [test_case.id] if with_cases else []
        with pytest.raises(ValueError, match=error):
            await case_fields_service.batch_get_fields(case_ids, [field_id])

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
            patch.object(
                case_fields_service, "normalize_field_values", return_value=fields_data
            ) as mock_normalize_fields,
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
            mock_normalize_fields.assert_called_once_with(fields_data)
            mock_update_row.assert_called_once()

            # Verify the call arguments
            call_kwargs = mock_update_row.call_args.kwargs
            assert "row_id" in call_kwargs
            assert "data" in call_kwargs
            assert call_kwargs["data"] == fields_data

    async def test_upsert_field_values_preserves_numeric_strings(
        self, case_fields_service: CaseFieldsService, test_case: Case
    ) -> None:
        """Numeric strings should be normalized to Decimal before persistence."""
        fields_data = {"numeric_field": "1.30"}
        mock_result = {
            "id": uuid.uuid4(),
            "case_id": test_case.id,
            "numeric_field": Decimal("1.30"),
        }

        with (
            patch.object(
                case_fields_service, "ensure_workspace_row"
            ) as mock_ensure_row,
            patch.object(
                case_fields_service,
                "get_field_schema",
                return_value={"numeric_field": {"type": "NUMERIC"}},
            ),
            patch.object(
                case_fields_service.editor,
                "get_columns",
                return_value=[{"name": "numeric_field", "type": "NUMERIC"}],
            ),
            patch.object(case_fields_service.editor, "update_row") as mock_update_row,
        ):
            mock_ensure_row.return_value = uuid.uuid4()
            mock_update_row.return_value = mock_result

            result = await case_fields_service.upsert_field_values(
                test_case, fields_data
            )

            assert result == mock_result
            call_kwargs = mock_update_row.call_args.kwargs
            assert call_kwargs["data"] == {"numeric_field": Decimal("1.30")}

    async def test_normalize_field_values_reflection_overrides_stale_schema(
        self, case_fields_service: CaseFieldsService
    ) -> None:
        """Physical column types should win when schema metadata is stale."""
        with (
            patch.object(
                case_fields_service,
                "get_field_schema",
                return_value={"numeric_field": {"type": "TEXT"}},
            ),
            patch.object(
                case_fields_service.editor,
                "get_columns",
                return_value=[{"name": "numeric_field", "type": "NUMERIC"}],
            ),
        ):
            normalized = await case_fields_service.normalize_field_values(
                {"numeric_field": "1.30"}
            )

        assert normalized == {"numeric_field": Decimal("1.30")}

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


@pytest.mark.anyio
@pytest.mark.parametrize(
    ("sql_type", "default"),
    [
        pytest.param(SqlType.JSONB, '{"x":1}', id="jsonb-string-with-quotes"),
        pytest.param(SqlType.JSONB, {"a": 'b"c', "d": "e\\f"}, id="jsonb-object"),
        pytest.param(SqlType.MULTI_SELECT, ['a"b', "c\\d"], id="multi-select"),
        pytest.param(SqlType.TEXT, "C:\\tmp\\file", id="text-backslashes"),
        pytest.param(SqlType.TEXT, "it's", id="text-single-quote"),
    ],
)
async def test_render_default_value_round_trips(
    session: AsyncSession, sql_type: SqlType, default: Any
) -> None:
    """Rendered defaults must evaluate in PostgreSQL back to the original value."""
    rendered = render_default_value(sql_type, default)
    conn = await session.connection()
    result = await conn.exec_driver_sql(f"SELECT ({rendered})::text")
    value = result.scalar_one()
    if sql_type is SqlType.TEXT:
        assert value == default
    else:
        assert orjson.loads(value) == default
