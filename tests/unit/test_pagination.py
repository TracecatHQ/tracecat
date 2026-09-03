import base64
import json
from datetime import UTC, datetime
from decimal import Decimal
from enum import StrEnum
from typing import Any, TypedDict, cast
from uuid import UUID

import pytest
import sqlalchemy as sa
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column

from tracecat import config
from tracecat.pagination import (
    BaseCursorPaginator,
    CursorData,
    CursorPaginationParams,
    PageParams,
    PaginationConfigurationError,
    PaginationError,
    PaginationErrorCode,
    paginate,
)

pytestmark = pytest.mark.usefixtures("db")


class _PageItemValues(TypedDict):
    id: int
    score: int | None
    label: str


class _Priority(StrEnum):
    LOW = "low"
    HIGH = "high"


class _TypedPageItemValues(TypedDict):
    id: UUID
    priority: _Priority
    amount: Decimal
    created_at: datetime


class _FloatPageItemValues(TypedDict):
    id: int
    value: float


class _PageItemBase(DeclarativeBase):
    pass


class _MappedPageItem(_PageItemBase):
    __tablename__ = "test_mapped_page_items"
    __table_args__ = {"prefixes": ["TEMPORARY"]}

    id: Mapped[int] = mapped_column(primary_key=True)
    label: Mapped[str]


async def _create_page_items(session: AsyncSession) -> sa.Table:
    metadata = sa.MetaData()
    items = sa.Table(
        "test_page_items",
        metadata,
        sa.Column("id", sa.Integer, primary_key=True),
        sa.Column("score", sa.Integer, nullable=True),
        sa.Column("label", sa.String, nullable=False),
        prefixes=["TEMPORARY"],
    )
    connection = await session.connection()
    await connection.run_sync(metadata.create_all)
    values: list[_PageItemValues] = [
        {"id": 1, "score": 10, "label": "2024-01-01"},
        {"id": 2, "score": 10, "label": "2024-01-02"},
        {"id": 3, "score": 20, "label": "bravo"},
        {"id": 4, "score": 20, "label": "charlie"},
        {"id": 5, "score": None, "label": "delta"},
        {"id": 6, "score": None, "label": "echo"},
    ]
    await session.execute(items.insert(), values)
    return items


async def _create_typed_page_items(session: AsyncSession) -> sa.Table:
    metadata = sa.MetaData()
    items = sa.Table(
        "test_typed_page_items",
        metadata,
        sa.Column("id", sa.Uuid, primary_key=True),
        sa.Column("priority", sa.Enum(_Priority, native_enum=False), nullable=False),
        sa.Column("amount", sa.Numeric, nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        prefixes=["TEMPORARY"],
    )
    connection = await session.connection()
    await connection.run_sync(metadata.create_all)
    values: list[_TypedPageItemValues] = [
        {
            "id": UUID("00000000-0000-4000-8000-000000000001"),
            "priority": _Priority.LOW,
            "amount": Decimal("10.25"),
            "created_at": datetime(2026, 1, 1, tzinfo=UTC),
        },
        {
            "id": UUID("00000000-0000-4000-8000-000000000002"),
            "priority": _Priority.HIGH,
            "amount": Decimal("20.50"),
            "created_at": datetime(2026, 1, 2, tzinfo=UTC),
        },
        {
            "id": UUID("00000000-0000-4000-8000-000000000003"),
            "priority": _Priority.HIGH,
            "amount": Decimal("20.50"),
            "created_at": datetime(2026, 1, 3, tzinfo=UTC),
        },
    ]
    await session.execute(items.insert(), values)
    return items


@pytest.fixture
def pagination_signing_secret(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(config, "TRACECAT__SIGNING_SECRET", "pagination-test-secret")


class TestCursorPaginator:
    """Test the base cursor paginator functionality."""

    def test_encode_decode_cursor(self, session: AsyncSession):
        """Test cursor encoding and decoding."""
        paginator = BaseCursorPaginator(session)
        timestamp = datetime.now(UTC)
        entity_id = "test-id-123"

        # Test encoding with datetime sort value
        cursor = paginator.encode_cursor(
            entity_id, sort_column="created_at", sort_value=timestamp
        )
        assert isinstance(cursor, str)
        assert cursor  # Not empty

        # Test decoding
        decoded = paginator.decode_cursor(cursor)
        assert isinstance(decoded, CursorData)
        assert decoded.sort_column == "created_at"
        assert decoded.sort_value == timestamp
        assert decoded.id == entity_id

    def test_decode_invalid_cursor(self, session: AsyncSession):
        """Test decoding invalid cursors."""
        paginator = BaseCursorPaginator(session)

        # Test invalid base64
        with pytest.raises(ValueError, match="Invalid cursor format"):
            paginator.decode_cursor("invalid-base64!")

        # Test invalid JSON structure
        invalid_json = base64.urlsafe_b64encode(b"invalid json").decode()
        with pytest.raises(ValueError, match="Invalid cursor format"):
            paginator.decode_cursor(invalid_json)

        # Test missing fields
        incomplete_data = base64.urlsafe_b64encode(
            json.dumps({"ts": "2024-01-01T00:00:00Z"}).encode()
        ).decode()
        with pytest.raises(ValueError, match="Invalid cursor format"):
            paginator.decode_cursor(incomplete_data)

    def test_cursor_roundtrip_with_microseconds(self, session: AsyncSession):
        """Test that cursor encoding preserves microsecond precision."""
        paginator = BaseCursorPaginator(session)
        # Create timestamp with microseconds
        timestamp = datetime.now(UTC).replace(microsecond=123456)
        entity_id = "test-id"

        cursor = paginator.encode_cursor(
            entity_id, sort_column="created_at", sort_value=timestamp
        )
        decoded = paginator.decode_cursor(cursor)

        assert decoded.sort_column == "created_at"
        assert decoded.sort_value == timestamp
        assert isinstance(decoded.sort_value, datetime)
        assert decoded.sort_value.microsecond == 123456
        assert decoded.id == entity_id

    def test_cursor_pagination_params(self):
        """Test cursor pagination parameters."""
        # Test default values
        params = CursorPaginationParams()
        assert params.cursor is None
        assert params.limit == 20

        # Test with custom values
        params = CursorPaginationParams(cursor="test-cursor", limit=50)
        assert params.cursor == "test-cursor"
        assert params.limit == 50

        # Test validation
        with pytest.raises(ValueError):
            CursorPaginationParams(limit=0)  # Below minimum

        with pytest.raises(ValueError):
            CursorPaginationParams(limit=201)  # Above maximum

    def test_cursor_data_model(self):
        """Test CursorData model validation."""
        timestamp = datetime.now(UTC)
        entity_id = "test-id"

        cursor_data = CursorData(
            sort_column="created_at", sort_value=timestamp, id=entity_id
        )
        assert cursor_data.sort_column == "created_at"
        assert cursor_data.sort_value == timestamp
        assert cursor_data.id == entity_id

        # Test JSON serialization
        json_data = cursor_data.model_dump_json()
        assert isinstance(json_data, str)

        # Test deserialization
        parsed_data = json.loads(json_data)
        recreated = CursorData.model_validate(parsed_data)
        assert recreated.sort_column == "created_at"
        assert recreated.sort_value == timestamp
        assert recreated.id == entity_id


class TestPaginate:
    """Tests for the deep keyset pagination API."""

    @pytest.mark.anyio
    async def test_single_orm_entity_is_the_default_item(
        self,
        session: AsyncSession,
        pagination_signing_secret: None,
    ) -> None:
        connection = await session.connection()
        await connection.run_sync(_PageItemBase.metadata.create_all)
        item = _MappedPageItem(id=1, label="first")
        session.add(item)
        await session.flush()

        page = await paginate(
            session,
            sa.select(_MappedPageItem),
            page=PageParams(),
            order_by=(_MappedPageItem.id.asc(),),
        )

        assert [result.id for result in page.items] == [item.id]

    @pytest.mark.anyio
    async def test_forward_and_backward_composite_pagination(
        self,
        session: AsyncSession,
        pagination_signing_secret: None,
    ) -> None:
        items = await _create_page_items(session)
        statement = sa.select(items.c.id)
        order_by = (
            items.c.score.desc().nulls_last(),
            items.c.id.desc(),
        )

        first = await paginate(
            session,
            statement,
            page=PageParams(limit=2),
            order_by=order_by,
        )
        assert first.items == [4, 3]
        assert first.next_cursor is not None
        assert first.prev_cursor is None
        assert first.has_more is True
        assert first.has_previous is False

        second = await paginate(
            session,
            statement,
            page=PageParams(limit=2, cursor=first.next_cursor),
            order_by=order_by,
        )
        assert second.items == [2, 1]
        assert second.next_cursor is not None
        assert second.prev_cursor is not None

        third = await paginate(
            session,
            statement,
            page=PageParams(limit=2, cursor=second.next_cursor),
            order_by=order_by,
        )
        assert third.items == [6, 5]
        assert third.next_cursor is None
        assert third.prev_cursor is not None

        back_to_second = await paginate(
            session,
            statement,
            page=PageParams(limit=2, cursor=third.prev_cursor),
            order_by=order_by,
        )
        assert back_to_second.items == second.items
        assert back_to_second.next_cursor is not None
        assert back_to_second.prev_cursor is not None

        back_to_first = await paginate(
            session,
            statement,
            page=PageParams(limit=2, cursor=back_to_second.prev_cursor),
            order_by=order_by,
        )
        assert back_to_first.items == first.items
        assert back_to_first.next_cursor is not None
        assert back_to_first.prev_cursor is None

    @pytest.mark.anyio
    async def test_mixed_directions_and_default_nulls_last(
        self,
        session: AsyncSession,
        pagination_signing_secret: None,
    ) -> None:
        items = await _create_page_items(session)
        statement = sa.select(items.c.id)
        order_by = (items.c.score.asc(), items.c.id.desc())

        seen: list[int] = []
        cursor: str | None = None
        while True:
            page = await paginate(
                session,
                statement,
                page=PageParams(limit=2, cursor=cursor),
                order_by=order_by,
            )
            seen.extend(page.items)
            if page.next_cursor is None:
                break
            cursor = page.next_cursor

        assert seen == [2, 1, 4, 3, 6, 5]

    @pytest.mark.parametrize("score_direction", ["asc", "desc"])
    @pytest.mark.parametrize("null_placement", ["first", "last"])
    @pytest.mark.parametrize("id_direction", ["asc", "desc"])
    @pytest.mark.parametrize("limit", [1, 2, 4])
    @pytest.mark.anyio
    async def test_all_ordering_combinations_round_trip(
        self,
        session: AsyncSession,
        pagination_signing_secret: None,
        score_direction: str,
        null_placement: str,
        id_direction: str,
        limit: int,
    ) -> None:
        items = await _create_page_items(session)
        statement = sa.select(items.c.id)
        score_order = (
            items.c.score.asc() if score_direction == "asc" else items.c.score.desc()
        )
        score_order = (
            score_order.nulls_first()
            if null_placement == "first"
            else score_order.nulls_last()
        )
        id_order = items.c.id.asc() if id_direction == "asc" else items.c.id.desc()
        order_by = (score_order, id_order)
        expected = list(
            (await session.execute(statement.order_by(*order_by))).scalars().all()
        )

        pages = []
        cursor: str | None = None
        while True:
            page = await paginate(
                session,
                statement,
                page=PageParams(limit=limit, cursor=cursor),
                order_by=order_by,
            )
            pages.append(page)
            if page.next_cursor is None:
                break
            cursor = page.next_cursor

        assert [item for page in pages for item in page.items] == expected
        current = pages[-1]
        for expected_page in reversed(pages[:-1]):
            assert current.prev_cursor is not None
            current = await paginate(
                session,
                statement,
                page=PageParams(limit=limit, cursor=current.prev_cursor),
                order_by=order_by,
            )
            assert current.items == expected_page.items
        assert current.prev_cursor is None

    @pytest.mark.anyio
    async def test_string_cursor_values_are_decoded_by_column_type(
        self,
        session: AsyncSession,
        pagination_signing_secret: None,
    ) -> None:
        items = await _create_page_items(session)
        statement = sa.select(items.c.label)
        order_by = (items.c.label.asc(), items.c.id.asc())

        first = await paginate(
            session,
            statement,
            page=PageParams(limit=1),
            order_by=order_by,
        )
        second = await paginate(
            session,
            statement,
            page=PageParams(limit=1, cursor=first.next_cursor),
            order_by=order_by,
        )

        assert first.items == ["2024-01-01"]
        assert second.items == ["2024-01-02"]

    @pytest.mark.anyio
    async def test_common_scalar_cursor_types_round_trip(
        self,
        session: AsyncSession,
        pagination_signing_secret: None,
    ) -> None:
        items = await _create_typed_page_items(session)
        statement = sa.select(items.c.id)
        order_by = (
            items.c.priority.asc(),
            items.c.amount.desc(),
            items.c.created_at.desc(),
            items.c.id.asc(),
        )
        expected = list(
            (await session.execute(statement.order_by(*order_by))).scalars().all()
        )

        seen: list[UUID] = []
        cursor: str | None = None
        while True:
            page = await paginate(
                session,
                statement,
                page=PageParams(limit=1, cursor=cursor),
                order_by=order_by,
            )
            seen.extend(page.items)
            if page.next_cursor is None:
                break
            cursor = page.next_cursor

        assert seen == expected

    @pytest.mark.anyio
    async def test_binary_cursor_values_round_trip(
        self,
        session: AsyncSession,
        pagination_signing_secret: None,
    ) -> None:
        metadata = sa.MetaData()
        items = sa.Table(
            "test_binary_page_items",
            metadata,
            sa.Column("id", sa.Integer, primary_key=True),
            sa.Column("value", sa.LargeBinary, nullable=False),
            prefixes=["TEMPORARY"],
        )
        connection = await session.connection()
        await connection.run_sync(metadata.create_all)
        await session.execute(
            items.insert(),
            [
                {"id": 1, "value": b"\xff\x00"},
                {"id": 2, "value": b"\x00"},
            ],
        )
        statement = sa.select(items.c.id)
        order_by = (items.c.value.desc(), items.c.id.asc())

        first = await paginate(
            session,
            statement,
            page=PageParams(limit=1),
            order_by=order_by,
        )
        assert first.items == [1]
        assert first.next_cursor is not None

        second = await paginate(
            session,
            statement,
            page=PageParams(limit=1, cursor=first.next_cursor),
            order_by=order_by,
        )
        assert second.items == [2]
        assert second.prev_cursor is not None

        back_to_first = await paginate(
            session,
            statement,
            page=PageParams(limit=1, cursor=second.prev_cursor),
            order_by=order_by,
        )
        assert back_to_first.items == first.items

    @pytest.mark.anyio
    async def test_json_cursor_values_accept_every_json_shape(
        self,
        session: AsyncSession,
        pagination_signing_secret: None,
    ) -> None:
        metadata = sa.MetaData()
        items = sa.Table(
            "test_json_page_items",
            metadata,
            sa.Column("id", sa.Integer, primary_key=True),
            sa.Column("value", JSONB, nullable=False),
            prefixes=["TEMPORARY"],
        )
        connection = await session.connection()
        await connection.run_sync(metadata.create_all)
        await session.execute(
            items.insert(),
            [
                {"id": 1, "value": ["alpha"]},
                {"id": 2, "value": "bravo"},
                {"id": 3, "value": 42},
            ],
        )
        statement = sa.select(items.c.id)
        order_by = (items.c.value.asc(), items.c.id.asc())
        expected = list(
            (await session.execute(statement.order_by(*order_by))).scalars().all()
        )

        seen: list[int] = []
        cursor: str | None = None
        while True:
            page = await paginate(
                session,
                statement,
                page=PageParams(limit=1, cursor=cursor),
                order_by=order_by,
            )
            seen.extend(page.items)
            if page.next_cursor is None:
                break
            cursor = page.next_cursor

        assert seen == expected

    @pytest.mark.anyio
    async def test_database_equal_numeric_values_require_unique_tie_breaker(
        self,
        session: AsyncSession,
        pagination_signing_secret: None,
    ) -> None:
        metadata = sa.MetaData()
        items = sa.Table(
            "test_numeric_page_items",
            metadata,
            sa.Column("id", sa.Integer, primary_key=True),
            sa.Column("amount", sa.Numeric, nullable=False),
            prefixes=["TEMPORARY"],
        )
        connection = await session.connection()
        await connection.run_sync(metadata.create_all)
        await session.execute(items.insert().values(id=1, amount=Decimal("1.0")))
        await session.execute(items.insert().values(id=2, amount=Decimal("1.00")))
        statement = sa.select(items.c.id)

        with pytest.raises(
            PaginationConfigurationError,
            match="unique tie-breaker",
        ):
            await paginate(
                session,
                statement,
                page=PageParams(limit=1),
                order_by=(items.c.amount.asc(),),
            )

        first = await paginate(
            session,
            statement,
            page=PageParams(limit=1),
            order_by=(items.c.amount.asc(), items.c.id.asc()),
        )
        assert first.items == [1]
        assert first.next_cursor is not None
        second = await paginate(
            session,
            statement,
            page=PageParams(limit=1, cursor=first.next_cursor),
            order_by=(items.c.amount.asc(), items.c.id.asc()),
        )
        assert second.items == [2]

    @pytest.mark.anyio
    async def test_non_finite_float_cursor_values_round_trip(
        self,
        session: AsyncSession,
        pagination_signing_secret: None,
    ) -> None:
        metadata = sa.MetaData()
        items = sa.Table(
            "test_float_page_items",
            metadata,
            sa.Column("id", sa.Integer, primary_key=True),
            sa.Column("value", sa.Float, nullable=False),
            prefixes=["TEMPORARY"],
        )
        connection = await session.connection()
        await connection.run_sync(metadata.create_all)
        values: list[_FloatPageItemValues] = [
            {"id": 1, "value": float("-inf")},
            {"id": 2, "value": 0.0},
            {"id": 3, "value": float("inf")},
            {"id": 4, "value": float("nan")},
        ]
        await session.execute(items.insert(), values)
        statement = sa.select(items.c.id)
        order_by = (items.c.value.asc(), items.c.id.asc())

        pages = []
        cursor: str | None = None
        while True:
            page = await paginate(
                session,
                statement,
                page=PageParams(limit=1, cursor=cursor),
                order_by=order_by,
            )
            pages.append(page)
            if page.next_cursor is None:
                break
            cursor = page.next_cursor

        assert [item for page in pages for item in page.items] == [1, 2, 3, 4]
        current = pages[-1]
        for expected_page in reversed(pages[:-1]):
            assert current.prev_cursor is not None
            current = await paginate(
                session,
                statement,
                page=PageParams(limit=1, cursor=current.prev_cursor),
                order_by=order_by,
            )
            assert current.items == expected_page.items

        await session.execute(items.insert().values(id=5, value=float("nan")))
        with pytest.raises(
            PaginationConfigurationError,
            match="unique tie-breaker",
        ):
            await paginate(
                session,
                statement,
                page=PageParams(limit=4),
                order_by=(items.c.value.asc(),),
            )

    @pytest.mark.anyio
    async def test_row_factory_hides_private_cursor_columns(
        self,
        session: AsyncSession,
        pagination_signing_secret: None,
    ) -> None:
        items = await _create_page_items(session)
        statement = sa.select(items.c.id, items.c.label)

        def build_item(row: tuple[object, ...]) -> tuple[int, str]:
            assert len(row) == 2
            return cast(int, row[0]), cast(str, row[1])

        page = await paginate(
            session,
            statement,
            page=PageParams(limit=2),
            order_by=(items.c.id.asc(),),
            row_factory=build_item,
        )

        assert page.items == [(1, "2024-01-01"), (2, "2024-01-02")]

    @pytest.mark.anyio
    async def test_computed_ordering_with_distinct_projection(
        self,
        session: AsyncSession,
        pagination_signing_secret: None,
    ) -> None:
        items = await _create_page_items(session)
        statement = sa.select(items.c.id, items.c.label).distinct()
        computed_score = sa.func.coalesce(items.c.score, -1)

        def get_id(row: tuple[object, ...]) -> int:
            return cast(int, row[0])

        seen: list[int] = []
        cursor: str | None = None
        while True:
            page = await paginate(
                session,
                statement,
                page=PageParams(limit=2, cursor=cursor),
                order_by=(computed_score.desc(), items.c.id.desc()),
                row_factory=get_id,
            )
            seen.extend(page.items)
            if page.next_cursor is None:
                break
            cursor = page.next_cursor

        assert seen == [4, 3, 2, 1, 6, 5]

    @pytest.mark.anyio
    async def test_grouped_aggregate_ordering_round_trips(
        self,
        session: AsyncSession,
        pagination_signing_secret: None,
    ) -> None:
        items = await _create_page_items(session)
        item_count = sa.func.count(items.c.id)
        statement = sa.select(items.c.score).group_by(items.c.score)
        order_by = (item_count.desc(), items.c.score.asc().nulls_last())

        seen: list[int | None] = []
        cursor: str | None = None
        while True:
            page = await paginate(
                session,
                statement,
                page=PageParams(limit=1, cursor=cursor),
                order_by=order_by,
            )
            seen.extend(page.items)
            if page.next_cursor is None:
                break
            cursor = page.next_cursor

        assert seen == [10, 20, None]

    @pytest.mark.anyio
    async def test_cursor_is_bound_to_query_filters(
        self,
        session: AsyncSession,
        pagination_signing_secret: None,
    ) -> None:
        items = await _create_page_items(session)
        statement = sa.select(items.c.id)
        order_by = (items.c.id.asc(),)
        first = await paginate(
            session,
            statement,
            page=PageParams(limit=2),
            order_by=order_by,
        )
        assert first.next_cursor is not None

        changed_statement = statement.where(items.c.score == 20)
        with pytest.raises(PaginationError) as exc_info:
            await paginate(
                session,
                changed_statement,
                page=PageParams(limit=2, cursor=first.next_cursor),
                order_by=order_by,
            )

        assert exc_info.value.code is PaginationErrorCode.CURSOR_MISMATCH

    @pytest.mark.anyio
    async def test_cursor_accepts_semantically_equivalent_in_filter_order(
        self,
        session: AsyncSession,
        pagination_signing_secret: None,
    ) -> None:
        items = await _create_page_items(session)
        first_statement = sa.select(items.c.id).where(items.c.id.in_([4, 3, 2, 1]))
        order_by = (items.c.id.asc(),)
        first = await paginate(
            session,
            first_statement,
            page=PageParams(limit=2),
            order_by=order_by,
        )
        assert first.next_cursor is not None

        equivalent_statement = sa.select(items.c.id).where(items.c.id.in_([1, 2, 3, 4]))
        second = await paginate(
            session,
            equivalent_statement,
            page=PageParams(limit=2, cursor=first.next_cursor),
            order_by=order_by,
        )

        assert second.items == [3, 4]

    @pytest.mark.anyio
    async def test_cursor_is_bound_to_execution_options(
        self,
        session: AsyncSession,
        pagination_signing_secret: None,
    ) -> None:
        items = await _create_page_items(session)
        statement = sa.select(items.c.id)
        order_by = (items.c.id.asc(),)
        first = await paginate(
            session,
            statement,
            page=PageParams(limit=2),
            order_by=order_by,
        )
        assert first.next_cursor is not None

        changed_statement = statement.execution_options(include_deleted=True)
        with pytest.raises(PaginationError) as exc_info:
            await paginate(
                session,
                changed_statement,
                page=PageParams(limit=2, cursor=first.next_cursor),
                order_by=order_by,
            )

        assert exc_info.value.code is PaginationErrorCode.CURSOR_MISMATCH

    @pytest.mark.anyio
    async def test_cursor_does_not_depend_on_anchor_row_existing(
        self,
        session: AsyncSession,
        pagination_signing_secret: None,
    ) -> None:
        items = await _create_page_items(session)
        statement = sa.select(items.c.id)
        order_by = (items.c.id.asc(),)
        first = await paginate(
            session,
            statement,
            page=PageParams(limit=2),
            order_by=order_by,
        )
        assert first.items == [1, 2]
        assert first.next_cursor is not None
        await session.execute(sa.delete(items).where(items.c.id == 2))

        second = await paginate(
            session,
            statement,
            page=PageParams(limit=2, cursor=first.next_cursor),
            order_by=order_by,
        )

        assert second.items == [3, 4]

    @pytest.mark.anyio
    async def test_empty_forward_page_can_recover_the_adjacent_surviving_page(
        self,
        session: AsyncSession,
        pagination_signing_secret: None,
    ) -> None:
        items = await _create_page_items(session)
        statement = sa.select(items.c.id)
        order_by = (items.c.id.asc(),)
        first = await paginate(
            session,
            statement,
            page=PageParams(limit=2),
            order_by=order_by,
        )
        assert first.items == [1, 2]
        assert first.next_cursor is not None
        await session.execute(sa.delete(items).where(items.c.id > 2))

        empty = await paginate(
            session,
            statement,
            page=PageParams(limit=2, cursor=first.next_cursor),
            order_by=order_by,
        )
        assert empty.items == []
        assert empty.next_cursor is None
        assert empty.prev_cursor is not None

        recovered = await paginate(
            session,
            statement,
            page=PageParams(limit=2, cursor=empty.prev_cursor),
            order_by=order_by,
        )
        assert recovered.items == [1, 2]
        assert recovered.next_cursor is None
        assert recovered.prev_cursor is None

    @pytest.mark.anyio
    async def test_empty_backward_page_can_recover_the_adjacent_surviving_page(
        self,
        session: AsyncSession,
        pagination_signing_secret: None,
    ) -> None:
        items = await _create_page_items(session)
        statement = sa.select(items.c.id)
        order_by = (items.c.id.asc(),)
        first = await paginate(
            session,
            statement,
            page=PageParams(limit=2),
            order_by=order_by,
        )
        second = await paginate(
            session,
            statement,
            page=PageParams(limit=2, cursor=first.next_cursor),
            order_by=order_by,
        )
        third = await paginate(
            session,
            statement,
            page=PageParams(limit=2, cursor=second.next_cursor),
            order_by=order_by,
        )
        assert third.items == [5, 6]
        assert third.prev_cursor is not None
        await session.execute(sa.delete(items).where(items.c.id < 5))

        empty = await paginate(
            session,
            statement,
            page=PageParams(limit=2, cursor=third.prev_cursor),
            order_by=order_by,
        )
        assert empty.items == []
        assert empty.next_cursor is not None
        assert empty.prev_cursor is None

        recovered = await paginate(
            session,
            statement,
            page=PageParams(limit=2, cursor=empty.next_cursor),
            order_by=order_by,
        )
        assert recovered.items == [5, 6]
        assert recovered.next_cursor is None
        assert recovered.prev_cursor is None

    @pytest.mark.anyio
    async def test_tampered_cursor_is_rejected(
        self,
        session: AsyncSession,
        pagination_signing_secret: None,
    ) -> None:
        items = await _create_page_items(session)
        statement = sa.select(items.c.id)
        order_by = (items.c.id.asc(),)
        first = await paginate(
            session,
            statement,
            page=PageParams(limit=2),
            order_by=order_by,
        )
        assert first.next_cursor is not None
        replacement = "A" if first.next_cursor[0] != "A" else "B"
        tampered = replacement + first.next_cursor[1:]

        with pytest.raises(PaginationError) as exc_info:
            await paginate(
                session,
                statement,
                page=PageParams(limit=2, cursor=tampered),
                order_by=order_by,
            )

        assert exc_info.value.code is PaginationErrorCode.INVALID_CURSOR
        assert isinstance(exc_info.value, ValueError)

    @pytest.mark.anyio
    async def test_oversized_cursor_fails_at_its_source(
        self,
        session: AsyncSession,
        pagination_signing_secret: None,
    ) -> None:
        items = await _create_page_items(session)
        await session.execute(items.insert().values(id=7, score=30, label="a" * 9000))
        await session.execute(items.insert().values(id=8, score=30, label="b" * 9000))

        with pytest.raises(
            PaginationConfigurationError,
            match="compact ordering keys",
        ):
            await paginate(
                session,
                sa.select(items.c.id).where(items.c.id.in_([7, 8])),
                page=PageParams(limit=1),
                order_by=(items.c.label.asc(), items.c.id.asc()),
            )

    @pytest.mark.anyio
    async def test_query_configuration_is_explicit(
        self,
        session: AsyncSession,
        pagination_signing_secret: None,
    ) -> None:
        items = await _create_page_items(session)

        with pytest.raises(PaginationConfigurationError, match=r"\.asc\(\)"):
            await paginate(
                session,
                sa.select(items.c.id),
                page=PageParams(),
                order_by=(items.c.id,),
            )

        with pytest.raises(PaginationConfigurationError, match="order_by"):
            await paginate(
                session,
                sa.select(items.c.id).order_by(items.c.id),
                page=PageParams(),
                order_by=(items.c.id.asc(),),
            )

        with pytest.raises(PaginationConfigurationError, match="bounds"):
            await paginate(
                session,
                sa.select(items.c.id),
                page=PageParams.model_construct(limit=0),
                order_by=(items.c.id.asc(),),
            )

        with pytest.raises(PaginationConfigurationError, match="FETCH"):
            await paginate(
                session,
                sa.select(items.c.id).fetch(1),
                page=PageParams(),
                order_by=(items.c.id.asc(),),
            )

        with pytest.raises(PaginationConfigurationError, match="row_factory"):
            await paginate(
                session,
                cast(Any, sa.select(items.c.id, items.c.label)),
                page=PageParams(),
                order_by=(items.c.id.asc(),),
            )

        with pytest.raises(
            PaginationConfigurationError,
            match="no Python value type",
        ):
            await paginate(
                session,
                sa.select(items.c.id),
                page=PageParams(),
                order_by=(sa.column("id").asc(),),
            )

        with pytest.raises(
            PaginationConfigurationError,
            match="unique tie-breaker",
        ):
            await paginate(
                session,
                sa.select(items.c.id),
                page=PageParams(limit=1),
                order_by=(items.c.score.asc(),),
            )
