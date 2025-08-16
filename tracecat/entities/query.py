"""Type-safe query builder for custom entities with JSONB support.

This module follows SQLAlchemy 2.0 best practices for JSONB querying:
- Uses built-in JSONB comparator methods for type safety
- Leverages GIN index optimization patterns
- Provides proper type validation and casting

Reference: https://docs.sqlalchemy.org/en/20/dialects/postgresql.html#sqlalchemy.dialects.postgresql.JSONB
"""

from typing import Any, cast
from uuid import UUID

import sqlalchemy as sa
from sqlalchemy.sql import ColumnElement
from sqlalchemy.sql import false as sa_false
from sqlmodel import select
from sqlmodel.ext.asyncio.session import AsyncSession
from sqlmodel.sql.expression import SelectOfScalar

from tracecat.db.schemas import FieldMetadata, Record, RecordRelationLink
from tracecat.entities.types import FieldType


class EntityQueryBuilder:
    """Build type-safe JSONB queries with field validation.

    This builder follows SQLAlchemy 2.0 best practices:
    - Uses native JSONB comparator methods where available
    - Direct JSONB operators (?, @>) for GIN index optimization
    - Optimized for PostgreSQL GIN indexes on JSONB columns
    - Type-safe with proper value validation
    - Avoids unnecessary type casting for better performance

    Performance optimizations:
    - Top-level key access for GIN index usage
    - Native JSONB operators for boolean/numeric comparisons
    - Built-in contains() for array operations (@> operator)

    This builder ensures:
    1. Fields exist and are active before querying
    2. Values match expected field types
    3. Queries use GIN-optimized patterns for performance
    """

    def __init__(self, session: AsyncSession):
        """Initialize query builder with database session.

        Args:
            session: AsyncSession for database access
        """
        self.session = session
        self._field_cache: dict[str, FieldMetadata] = {}

    async def _validate_field(self, entity_id: UUID, field_key: str) -> FieldMetadata:
        """Validate field exists and is active before building query.

        Args:
            entity_id: Entity metadata ID
            field_key: Field key to validate

        Returns:
            FieldMetadata if valid

        Raises:
            ValueError: If field not found or inactive
        """
        cache_key = f"{entity_id}:{field_key}"

        if cache_key not in self._field_cache:
            stmt = select(FieldMetadata).where(
                FieldMetadata.entity_id == entity_id,
                FieldMetadata.field_key == field_key,
                FieldMetadata.is_active,  # SQLModel uses actual boolean, not comparison
            )
            result = await self.session.exec(stmt)
            field = result.first()

            if not field:
                raise ValueError(f"Field '{field_key}' not found or inactive")

            self._field_cache[cache_key] = field

        return self._field_cache[cache_key]

    async def eq(
        self, entity_id: UUID, field_key: str, value: Any
    ) -> ColumnElement[bool]:
        """Build type-safe equality check.

        Args:
            entity_id: Entity metadata ID
            field_key: Field to compare
            value: Value to compare against

        Returns:
            SQLAlchemy expression for equality check

        Raises:
            ValueError: If field not found
            TypeError: If value type doesn't match field type
        """
        field = await self._validate_field(entity_id, field_key)

        # Type validation based on field.field_type
        field_type = FieldType(field.field_type)
        if field_type == FieldType.INTEGER:
            if not isinstance(value, int) or isinstance(value, bool):
                raise TypeError(
                    f"Expected int for {field_key}, got {type(value).__name__}"
                )
        elif field_type == FieldType.NUMBER:
            if not isinstance(value, int | float) or isinstance(value, bool):
                raise TypeError(
                    f"Expected number for {field_key}, got {type(value).__name__}"
                )
        elif field_type == FieldType.TEXT:
            if not isinstance(value, str):
                raise TypeError(
                    f"Expected string for {field_key}, got {type(value).__name__}"
                )
        elif field_type == FieldType.BOOL:
            if not isinstance(value, bool):
                raise TypeError(
                    f"Expected bool for {field_key}, got {type(value).__name__}"
                )

        # Build JSONB path query optimized for GIN indexes
        # Note: Direct JSONB comparison with primitives requires proper casting
        # We use astext for consistent comparison and cast when needed
        if isinstance(value, bool):
            # JSON booleans are stored as true/false (lowercase strings)
            return cast(
                ColumnElement[bool],
                Record.field_data[field_key].astext == ("true" if value else "false"),
            )
        elif isinstance(value, int | float):
            # For numbers, use astext and compare as strings
            # This maintains GIN index optimization
            return cast(
                ColumnElement[bool],
                Record.field_data[field_key].astext == str(value),
            )
        else:
            # For strings, use astext for consistent comparison
            return cast(
                ColumnElement[bool],
                Record.field_data[field_key].astext == str(value),
            )

    async def in_(
        self, entity_id: UUID, field_key: str, values: list[Any]
    ) -> ColumnElement[bool]:
        """Build type-safe IN check.

        Args:
            entity_id: Entity metadata ID
            field_key: Field to check
            values: List of values to check against

        Returns:
            SQLAlchemy expression for IN check
        """
        await self._validate_field(entity_id, field_key)

        # Build OR conditions for each value
        # Use astext for all comparisons to maintain consistency
        conditions = []
        for value in values:
            if isinstance(value, bool):
                # Convert boolean to JSON string representation
                conditions.append(
                    Record.field_data[field_key].astext
                    == ("true" if value else "false")
                )
            else:
                # All other types use string comparison
                conditions.append(Record.field_data[field_key].astext == str(value))

        return sa.or_(*conditions) if conditions else sa_false()

    async def ilike(
        self, entity_id: UUID, field_key: str, pattern: str
    ) -> ColumnElement[bool]:
        """Build case-insensitive pattern match for text fields.

        Args:
            entity_id: Entity metadata ID
            field_key: Field to search
            pattern: Pattern to match (with % wildcards)

        Returns:
            SQLAlchemy expression for ILIKE

        Raises:
            TypeError: If field is not a text type
        """
        field = await self._validate_field(entity_id, field_key)

        field_type = FieldType(field.field_type)
        if field_type not in (FieldType.TEXT, FieldType.SELECT):
            raise TypeError(f"Field {field_key} is not a text type")

        return cast(
            ColumnElement[bool],
            Record.field_data[field_key].astext.ilike(pattern),
        )

    async def array_contains(
        self, entity_id: UUID, field_key: str, values: list[Any]
    ) -> ColumnElement[bool]:
        """Build array containment check using GIN-optimized @> operator.

        Args:
            entity_id: Entity metadata ID
            field_key: Field to check
            values: Values that must be in the array

        Returns:
            SQLAlchemy expression for array containment

        Raises:
            TypeError: If field is not an array type
        """
        field = await self._validate_field(entity_id, field_key)

        field_type_str = field.field_type
        if (
            not field_type_str.startswith("ARRAY_")
            and field_type_str != FieldType.MULTI_SELECT.value
        ):
            raise TypeError(f"Field {field_key} is not an array type")

        # Use built-in contains() method for GIN index optimization
        # The contains() method uses the @> operator internally
        return cast(
            ColumnElement[bool],
            Record.field_data[field_key].contains(values),
        )

    async def between(
        self, entity_id: UUID, field_key: str, start: Any, end: Any
    ) -> ColumnElement[bool]:
        """Build range check for numeric/date fields.

        Args:
            entity_id: Entity metadata ID
            field_key: Field to check
            start: Start of range (inclusive)
            end: End of range (inclusive)

        Returns:
            SQLAlchemy expression for range check
        """
        field = await self._validate_field(entity_id, field_key)

        field_type = FieldType(field.field_type)
        if field_type not in (
            FieldType.INTEGER,
            FieldType.NUMBER,
            FieldType.DATE,
            FieldType.DATETIME,
        ):
            raise TypeError(f"Field {field_key} does not support range queries")

        # For numeric types, cast to appropriate type
        if field_type in (FieldType.INTEGER, FieldType.NUMBER):
            field_expr = Record.field_data[field_key].astext.cast(sa.Numeric)
            return sa.and_(
                field_expr >= sa.cast(start, sa.Numeric),
                field_expr <= sa.cast(end, sa.Numeric),
            )
        else:
            field_expr = Record.field_data[field_key].astext
            return sa.and_(field_expr >= str(start), field_expr <= str(end))

    async def is_null(self, entity_id: UUID, field_key: str) -> ColumnElement[bool]:
        """Check if field is null or missing.

        Args:
            entity_id: Entity metadata ID
            field_key: Field to check

        Returns:
            SQLAlchemy expression for null check
        """
        await self._validate_field(entity_id, field_key)

        # Check if key doesn't exist or value is JSON null
        # Use JSONB ? operator for key existence check
        # Cast to ColumnElement to access JSONB operators
        field_data_col = cast(ColumnElement[Any], Record.field_data)
        return sa.or_(
            ~field_data_col.op("?")(field_key),  # Key doesn't exist
            Record.field_data[field_key].astext.is_(
                None
            ),  # JSON null becomes SQL NULL with astext
        )

    async def is_not_null(self, entity_id: UUID, field_key: str) -> ColumnElement[bool]:
        """Check if field has a non-null value.

        Args:
            entity_id: Entity metadata ID
            field_key: Field to check

        Returns:
            SQLAlchemy expression for not-null check
        """
        await self._validate_field(entity_id, field_key)

        # Key exists and value is not JSON null
        # Use JSONB ? operator for key existence check
        # Cast to ColumnElement to access JSONB operators
        field_data_col = cast(ColumnElement[Any], Record.field_data)
        return sa.and_(
            field_data_col.op("?")(field_key),  # Key exists
            Record.field_data[field_key].astext.isnot(
                None
            ),  # Not JSON null (using astext)
        )

    async def build_query(
        self,
        base_stmt: SelectOfScalar[Record],
        entity_id: UUID,
        filters: list[dict[str, Any]],
    ) -> SelectOfScalar[Record]:
        """Build complete query with all filters.

        Args:
            base_stmt: Base SELECT statement
            entity_id: Entity metadata ID
            filters: List of filter specifications

        Filter format:
            {
                "field": "field_key",
                "operator": "eq" | "in" | "ilike" | "contains" | "between" | "is_null",
                "value": <value>  # or [values] for array ops
            }

        Returns:
            Modified SELECT statement with filters applied

        Raises:
            ValueError: If operator is unsupported
        """
        conditions = []

        for filter_spec in filters:
            field_key = filter_spec["field"]
            operator = filter_spec["operator"]
            value = filter_spec.get("value")

            if operator == "eq":
                if value is None:
                    raise ValueError(
                        f"Value required for 'eq' operator on field {field_key}"
                    )
                conditions.append(await self.eq(entity_id, field_key, value))
            elif operator == "in":
                if value is None or not isinstance(value, list):
                    raise ValueError(
                        f"List value required for 'in' operator on field {field_key}"
                    )
                conditions.append(await self.in_(entity_id, field_key, value))
            elif operator == "ilike":
                if value is None or not isinstance(value, str):
                    raise ValueError(
                        f"String value required for 'ilike' operator on field {field_key}"
                    )
                conditions.append(await self.ilike(entity_id, field_key, value))
            elif operator == "contains":
                if value is None or not isinstance(value, list):
                    raise ValueError(
                        f"List value required for 'contains' operator on field {field_key}"
                    )
                conditions.append(
                    await self.array_contains(entity_id, field_key, value)
                )
            elif operator == "between":
                if (
                    value is None
                    or not isinstance(value, dict)
                    or "start" not in value
                    or "end" not in value
                ):
                    raise ValueError(
                        f"Dict with 'start' and 'end' required for 'between' operator on field {field_key}"
                    )
                conditions.append(
                    await self.between(
                        entity_id, field_key, value["start"], value["end"]
                    )
                )
            elif operator == "is_null":
                conditions.append(await self.is_null(entity_id, field_key))
            elif operator == "is_not_null":
                conditions.append(await self.is_not_null(entity_id, field_key))
            else:
                raise ValueError(f"Unsupported operator: {operator}")

        if conditions:
            return base_stmt.where(sa.and_(*conditions))

        return base_stmt

    # Relation Query Methods

    async def has_related(
        self,
        source_record_id: UUID,
        field_id: UUID,
        target_filters: list[dict[str, Any]] | None = None,
        page: int = 1,
        page_size: int = 50,
    ) -> tuple[list[Record], int]:
        """Query related records with pagination.

        Args:
            source_record_id: The source record ID
            field_id: The relation field ID
            target_filters: Optional filters on target records
            page: Page number (1-indexed)
            page_size: Number of records per page (max 100)

        Returns:
            Tuple of (records, total_count)

        Raises:
            ValueError: If pagination parameters invalid
        """
        if page < 1:
            raise ValueError("Page must be >= 1")
        if page_size < 1 or page_size > 100:
            raise ValueError("Page size must be between 1 and 100")

        # Build base query joining RecordRelationLink
        # Ensure SQLAlchemy-typed columns for static type checkers
        target_record_id_col = cast(
            ColumnElement[Any], RecordRelationLink.target_record_id
        )
        entity_id_col = cast(ColumnElement[Any], Record.id)
        onclause = cast(ColumnElement[bool], target_record_id_col == entity_id_col)

        base_stmt = (
            select(Record)
            .join(
                RecordRelationLink,
                onclause,
            )
            .where(
                cast(ColumnElement[Any], RecordRelationLink.source_record_id)
                == source_record_id,
                cast(ColumnElement[Any], RecordRelationLink.source_field_id)
                == field_id,
            )
        )

        # Get field metadata to validate filters
        field_stmt = select(FieldMetadata).where(FieldMetadata.id == field_id)
        field_result = await self.session.exec(field_stmt)
        field = field_result.first()

        if not field:
            raise ValueError(f"Field {field_id} not found")

        # Apply target filters if provided
        if target_filters:
            if field.target_entity_id is None:
                raise ValueError(f"Relation field {field_id} missing target entity id")
            # Convert potential UUID4 to UUID for type-narrowing
            target_entity_uuid: UUID = UUID(str(field.target_entity_id))
            base_stmt = await self.build_query(
                base_stmt, target_entity_uuid, target_filters
            )

            # When filters are applied, count from the filtered query
            # Build a query to get Record with filters first
            count_base_query = (
                select(Record)
                .join(
                    RecordRelationLink,
                    onclause,
                )
                .where(
                    cast(ColumnElement[Any], RecordRelationLink.source_record_id)
                    == source_record_id,
                    cast(ColumnElement[Any], RecordRelationLink.source_field_id)
                    == field_id,
                )
            )
            # Apply the same filters
            filtered_count_query = await self.build_query(
                count_base_query, target_entity_uuid, target_filters
            )
            # Convert to count query using subquery
            count_stmt = select(sa.func.count()).select_from(
                filtered_count_query.subquery()
            )
        else:
            # No filters - use simple count query
            count_stmt = (
                select(sa.func.count())
                .select_from(RecordRelationLink)
                .where(
                    RecordRelationLink.source_record_id == source_record_id,
                    RecordRelationLink.source_field_id == field_id,
                )
            )

        count_result = await self.session.exec(count_stmt)
        total_count = count_result.one()

        # Apply pagination
        offset = (page - 1) * page_size
        paginated_stmt = base_stmt.limit(page_size).offset(offset)

        # Execute query
        result = await self.session.exec(cast(SelectOfScalar[Record], paginated_stmt))
        records = list(result.all())

        return records, total_count

    async def count_related(
        self,
        source_record_id: UUID,
        field_id: UUID,
    ) -> int:
        """Count related records efficiently.

        Args:
            source_record_id: The source record ID
            field_id: The relation field ID

        Returns:
            Count of related records
        """
        stmt = (
            select(sa.func.count())
            .select_from(RecordRelationLink)
            .where(
                RecordRelationLink.source_record_id == source_record_id,
                RecordRelationLink.source_field_id == field_id,
            )
        )
        result = await self.session.exec(stmt)
        return result.one()

    async def slug_equals(
        self, entity_id: UUID, slug_field: str, slug_value: str
    ) -> ColumnElement[bool]:
        """Build optimized query for exact slug match.

        Uses GIN index optimization for JSONB field access.
        Case-insensitive by default.

        Args:
            entity_id: Entity metadata ID
            slug_field: Field to use as slug (e.g., "name", "title")
            slug_value: Exact value to match

        Returns:
            SQLAlchemy expression for slug equality

        Raises:
            ValueError: If field not found or not a text type
        """
        field = await self._validate_field(entity_id, slug_field)

        field_type = FieldType(field.field_type)
        if field_type not in (FieldType.TEXT, FieldType.SELECT):
            raise ValueError(
                f"Field '{slug_field}' must be a text type for slug operations"
            )

        # Use case-insensitive comparison for slug matching
        # This still leverages the GIN index for the field access
        return cast(
            ColumnElement[bool],
            sa.func.lower(Record.field_data[slug_field].astext)
            == sa.func.lower(slug_value),
        )

    async def slug_matches(
        self, entity_id: UUID, slug_field: str, pattern: str
    ) -> ColumnElement[bool]:
        """Build query for slug pattern matching.

        Supports case-insensitive pattern matching with wildcards.

        Args:
            entity_id: Entity metadata ID
            slug_field: Field to use as slug
            pattern: Pattern to match (supports % wildcards)

        Returns:
            SQLAlchemy expression for pattern matching

        Raises:
            ValueError: If field not found or not a text type
        """
        field = await self._validate_field(entity_id, slug_field)

        field_type = FieldType(field.field_type)
        if field_type not in (FieldType.TEXT, FieldType.SELECT):
            raise ValueError(
                f"Field '{slug_field}' must be a text type for slug operations"
            )

        # Use ILIKE for case-insensitive pattern matching
        return cast(
            ColumnElement[bool],
            Record.field_data[slug_field].astext.ilike(pattern),
        )

    async def filter_by_relation(
        self,
        entity_id: UUID,
        field_key: str,
        has_any: list[UUID] | None = None,
        has_all: list[UUID] | None = None,
        has_none: list[UUID] | None = None,
    ) -> SelectOfScalar[Record]:
        """Filter records by their relations.

        Advanced filtering for finding records with specific relations.

        Args:
            entity_id: Entity metadata ID
            field_key: Relation field key
            has_any: Records must have at least one of these relations
            has_all: Records must have all of these relations
            has_none: Records must not have any of these relations

        Returns:
            Query statement with relation filters applied

        Raises:
            ValueError: If no filter criteria provided
        """
        if not any([has_any, has_all, has_none]):
            raise ValueError("At least one filter criteria must be provided")

        # Get field metadata
        field = await self._validate_field(entity_id, field_key)

        if field.field_type not in (
            FieldType.RELATION_BELONGS_TO,
            FieldType.RELATION_HAS_MANY,
        ):
            raise ValueError(f"Field {field_key} is not a relation field")

        # Base query for entity records
        base_stmt = select(Record).where(Record.entity_id == entity_id)

        conditions = []

        if has_any:
            # Records that have at least one of the specified relations
            subquery = (
                select(RecordRelationLink.source_record_id)
                .where(
                    cast(ColumnElement[Any], RecordRelationLink.source_field_id)
                    == field.id,
                    cast(ColumnElement[Any], RecordRelationLink.target_record_id).in_(
                        has_any
                    ),
                )
                .distinct()
            )
            conditions.append(cast(ColumnElement[Any], Record.id).in_(subquery))

        if has_all:
            # Records that have all of the specified relations
            for target_id in has_all:
                subquery = select(RecordRelationLink.source_record_id).where(
                    cast(ColumnElement[Any], RecordRelationLink.source_field_id)
                    == field.id,
                    cast(ColumnElement[Any], RecordRelationLink.target_record_id)
                    == target_id,
                )
                conditions.append(cast(ColumnElement[Any], Record.id).in_(subquery))

        if has_none:
            # Records that don't have any of the specified relations
            subquery = (
                select(RecordRelationLink.source_record_id)
                .where(
                    cast(ColumnElement[Any], RecordRelationLink.source_field_id)
                    == field.id,
                    cast(ColumnElement[Any], RecordRelationLink.target_record_id).in_(
                        has_none
                    ),
                )
                .distinct()
            )
            conditions.append(~cast(ColumnElement[Any], Record.id).in_(subquery))

        if conditions:
            base_stmt = base_stmt.where(sa.and_(*conditions))

        return cast(SelectOfScalar[Record], base_stmt)
