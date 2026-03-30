import re
from collections.abc import Sequence
from datetime import UTC, date, datetime
from decimal import Decimal, InvalidOperation
from typing import Any

import orjson
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql
from sqlalchemy.dialects.postgresql import JSONB

from tracecat.tables.enums import SqlType

POSTGRES_BIGINT_MIN = -(2**63)
POSTGRES_BIGINT_MAX = 2**63 - 1


def is_valid_sql_type(type: str | SqlType) -> bool:
    """Check if the type is a valid SQL type for user-defined columns."""
    try:
        SqlType(type)
    except ValueError:
        return False
    return True


def coerce_to_utc_datetime(value: str | int | float | datetime | date) -> datetime:
    """Convert supported inputs into a timezone-aware UTC datetime."""
    if isinstance(value, datetime):
        dt = value
    elif isinstance(value, date):
        dt = datetime(value.year, value.month, value.day)
    elif isinstance(value, str):
        text = value.strip()
        if text.endswith(("Z", "z")):
            text = f"{text[:-1]}+00:00"
        try:
            dt = datetime.fromisoformat(text)
        except ValueError as exc:
            raise TypeError(f"Invalid ISO datetime string: {value!r}") from exc
    elif isinstance(value, int | float):
        dt = datetime.fromtimestamp(value, tz=UTC)
    else:
        raise TypeError(f"Unable to coerce {value!r} to UTC datetime")

    if dt.tzinfo is None:
        return dt.replace(tzinfo=UTC)
    return dt.astimezone(UTC)


def coerce_optional_to_utc_datetime(
    value: str | int | float | datetime | date | None,
) -> datetime | None:
    """Coerce a value to a timezone-aware UTC datetime."""
    if value is None:
        return None
    return coerce_to_utc_datetime(value)


def coerce_to_date(value: str | int | float | datetime | date) -> date:
    """Convert supported inputs into a date."""
    if isinstance(value, date) and not isinstance(value, datetime):
        return value
    if isinstance(value, datetime):
        return value.date()
    if isinstance(value, int | float):
        return datetime.fromtimestamp(value, tz=UTC).date()
    if isinstance(value, str):
        text = value.strip()
        try:
            parsed_dt = datetime.fromisoformat(text)
            return parsed_dt.date()
        except ValueError:
            try:
                return date.fromisoformat(text)
            except ValueError as exc:
                raise TypeError(f"Invalid ISO date string: {value!r}") from exc
    raise TypeError(f"Unable to coerce {value!r} to date")


def coerce_optional_to_date(
    value: str | int | float | datetime | date | None,
) -> date | None:
    """Coerce a value to a date."""
    if value is None:
        return None
    return coerce_to_date(value)


class InvalidDefaultValueError(ValueError):
    """Raised when a user-provided table default cannot be coerced safely."""


def _compile_sql_literal(value: Any, sql_type: sa.types.TypeEngine) -> str:
    expr = sa.literal(value, type_=sql_type)
    compiled = expr.compile(
        dialect=postgresql.dialect(),
        compile_kwargs={"literal_binds": True},
    )
    return str(compiled)


def coerce_default_value(type: SqlType, default: Any) -> Any:
    """Coerce a user-provided default into a validated Python value."""
    match type:
        case SqlType.MULTI_SELECT:
            return coerce_multi_select_value(default)
        case SqlType.JSONB:
            return default
        case SqlType.TEXT | SqlType.SELECT:
            return str(default)
        case SqlType.DATE:
            return coerce_to_date(default)
        case SqlType.TIMESTAMPTZ:
            return coerce_to_utc_datetime(default)
        case SqlType.BOOLEAN:
            if isinstance(default, bool):
                return default
            match str(default).lower():
                case "true" | "1":
                    return True
                case "false" | "0":
                    return False
                case _:
                    raise InvalidDefaultValueError(
                        f"Invalid boolean default value: {default!r}"
                    )
        case SqlType.INTEGER:
            try:
                decimal_value = Decimal(str(default))
            except (InvalidOperation, TypeError, ValueError) as exc:
                raise InvalidDefaultValueError(
                    f"Invalid integer default value: {default!r}"
                ) from exc
            if not decimal_value.is_finite():
                raise InvalidDefaultValueError(
                    f"Invalid integer default value: {default!r}"
                )
            if decimal_value != decimal_value.to_integral_value():
                raise InvalidDefaultValueError(
                    f"Invalid integer default value: {default!r}"
                )
            if (
                decimal_value < POSTGRES_BIGINT_MIN
                or decimal_value > POSTGRES_BIGINT_MAX
            ):
                raise InvalidDefaultValueError(
                    f"Invalid integer default value: {default!r}"
                )
            return int(decimal_value)
        case SqlType.NUMERIC:
            try:
                decimal_value = Decimal(str(default))
            except (InvalidOperation, TypeError, ValueError) as exc:
                raise InvalidDefaultValueError(
                    f"Invalid numeric default value: {default!r}"
                ) from exc
            if not decimal_value.is_finite():
                raise InvalidDefaultValueError(
                    f"Invalid numeric default value: {default!r}"
                )
            return decimal_value
        case _:
            raise InvalidDefaultValueError(
                f"Unsupported SQL type for default value: {type}"
            )


def normalize_default_value(type: SqlType, default: Any) -> Any:
    """Normalize a validated default into a JSON-serializable metadata value."""
    match type:
        case SqlType.MULTI_SELECT | SqlType.JSONB:
            return default
        case SqlType.BOOLEAN:
            return str(default).lower()
        case SqlType.DATE | SqlType.TIMESTAMPTZ:
            return default.isoformat()
        case SqlType.INTEGER | SqlType.NUMERIC | SqlType.TEXT | SqlType.SELECT:
            return str(default)
        case _:
            raise InvalidDefaultValueError(
                f"Unsupported SQL type for default value: {type}"
            )


def render_default_value(type: SqlType, default: Any) -> str:
    """Render a validated default as a SQL literal for PostgreSQL DDL.

    SECURITY NOTICE: Only used in a SQL DDL statement where parameter binding is not supported.
    """
    match type:
        case SqlType.MULTI_SELECT:
            json_literal = orjson.dumps(default).decode()
            return f"{_compile_sql_literal(json_literal, sa.String())}::jsonb"
        case SqlType.JSONB:
            json_literal = orjson.dumps(default).decode()
            return f"{_compile_sql_literal(json_literal, sa.String())}::jsonb"
        case SqlType.TEXT | SqlType.SELECT:
            return _compile_sql_literal(default, sa.String())
        case SqlType.DATE:
            return f"{_compile_sql_literal(default, sa.Date())}::date"
        case SqlType.TIMESTAMPTZ:
            rendered_default = _compile_sql_literal(
                default, sa.TIMESTAMP(timezone=True)
            )
            return f"{rendered_default}::timestamptz"
        case SqlType.BOOLEAN:
            return _compile_sql_literal(default, sa.Boolean())
        case SqlType.INTEGER:
            return _compile_sql_literal(default, sa.BigInteger())
        case SqlType.NUMERIC:
            return _compile_sql_literal(default, sa.Numeric())
        case _:
            raise InvalidDefaultValueError(
                f"Unsupported SQL type for default value: {type}"
            )


def prepare_default_value(type: SqlType, default: Any) -> tuple[Any, str]:
    """Validate a default once and return metadata + DDL representations."""
    coerced_default = coerce_default_value(type, default)
    normalized_default = normalize_default_value(type, coerced_default)
    rendered_default = render_default_value(type, coerced_default)
    return normalized_default, rendered_default


def handle_default_value(type: SqlType, default: Any) -> str:
    """Backward-compatible wrapper for SQL literal rendering."""
    _, rendered_default = prepare_default_value(type, default)
    return rendered_default


def to_sql_clause(value: Any, name: str, sql_type: SqlType) -> sa.BindParameter:
    """Convert a value to a SQL-compatible string based on type.

    Args:
        value: The value to convert to SQL format
        type: The SQL type to convert to

    Returns:
        A SQL-compatible string representation of the value

    Raises:
        TypeError: If the SQL type is not supported
    """
    match sql_type:
        case SqlType.SELECT:
            coerced = None if value is None else str(value)
            return sa.bindparam(key=name, value=coerced, type_=sa.String)
        case SqlType.MULTI_SELECT:
            coerced_list = None if value is None else coerce_multi_select_value(value)
            return sa.bindparam(key=name, value=coerced_list, type_=JSONB)
        case SqlType.JSONB:
            return sa.bindparam(key=name, value=value, type_=JSONB)
        case SqlType.TEXT:
            return sa.bindparam(key=name, value=str(value), type_=sa.String)
        case SqlType.DATE:
            coerced = coerce_optional_to_date(value)
            return sa.bindparam(key=name, value=coerced, type_=sa.Date)
        case SqlType.TIMESTAMPTZ:
            coerced = coerce_optional_to_utc_datetime(value)
            return sa.bindparam(
                key=name, value=coerced, type_=sa.TIMESTAMP(timezone=True)
            )
        case SqlType.BOOLEAN:
            # Allow bool, 1, 0 as valid boolean values
            match str(value).lower():
                case "true" | "1":
                    bool_value = True
                case "false" | "0":
                    bool_value = False
                case _:
                    raise TypeError(
                        f"Expected bool or 0/1, got {type(value).__name__}: {value}"
                    )
            return sa.bindparam(key=name, value=bool_value, type_=sa.Boolean)
        case SqlType.INTEGER:
            return sa.bindparam(key=name, value=value, type_=sa.BigInteger)
        case SqlType.NUMERIC:
            return sa.bindparam(key=name, value=value, type_=sa.Numeric)
        case _:
            raise TypeError(f"Unsupported SQL type for value conversion: {type}")


def parse_postgres_default(default_value: str | None) -> str | None:
    """Parse PostgreSQL default value expressions to extract the actual value.

    PostgreSQL stores default values as SQL expressions with type casts like:
    - 'attack'::text -> attack
    - 0::integer -> 0
    - true::boolean -> true
    - '2024-01-01'::timestamp -> 2024-01-01

    Args:
        default_value: The raw default value from PostgreSQL column reflection

    Returns:
        The parsed default value without type casts, or None if input is None
    """
    if default_value is None:
        return None

    # Remove a trailing PostgreSQL type cast suffix (e.g., ::text, ::timestamp)
    # Only strip if the cast appears at the end of the expression to avoid
    # breaking values like nextval('seq'::regclass)
    cast_suffix_pattern = re.compile(r"::[A-Za-z_][\w\. ]*(\[\])?\s*$")
    # Strip multiple trailing casts if present (e.g., 'x'::text::text)
    while cast_suffix_pattern.search(default_value):
        default_value = cast_suffix_pattern.sub("", default_value)

    # Remove surrounding quotes if present
    if default_value.startswith("'") and default_value.endswith("'"):
        default_value = default_value[1:-1].replace("''", "'")

    return default_value


def convert_value(value: str | None, type: SqlType) -> Any:
    if value is None:
        return None
    try:
        match type:
            case SqlType.INTEGER:
                return int(value)
            case SqlType.NUMERIC:
                return float(value)
            case SqlType.BOOLEAN:
                match value.lower():
                    case "true" | "1":
                        return True
                    case "false" | "0":
                        return False
                    case _:
                        raise TypeError(f"Invalid boolean value: {value}")
            case SqlType.JSONB:
                return orjson.loads(value)
            case SqlType.TEXT | SqlType.SELECT:
                return str(value)
            case SqlType.MULTI_SELECT:
                parsed = orjson.loads(value)
                return coerce_multi_select_value(parsed)
            case SqlType.DATE:
                return coerce_to_date(value)
            case SqlType.TIMESTAMPTZ:
                return coerce_to_utc_datetime(value)
            case _:
                raise TypeError(f"Unsupported SQL type for value conversion: {type}")
    except Exception as e:
        raise TypeError(
            f"Cannot convert value {value!r} to {type.__class__.__name__} {type.value}"
        ) from e


def normalize_column_options(
    options: Sequence[str] | None,
) -> list[str] | None:
    """Trim, deduplicate, and validate option labels."""
    if options is None:
        return None
    cleaned: list[str] = []
    seen: set[str] = set()
    for raw in options:
        candidate = raw.strip()
        if not candidate:
            raise ValueError("Option labels cannot be empty")
        if candidate in seen:
            raise ValueError("Duplicate option labels are not allowed")
        seen.add(candidate)
        cleaned.append(candidate)
    return cleaned


def coerce_select_value(value: Any, *, options: Sequence[str] | None = None) -> str:
    """Normalize a SELECT value ensuring membership in options if provided."""
    coerced = str(value)
    if options and coerced not in options:
        raise ValueError(f"Value '{coerced}' is not in the available options")
    return coerced


def coerce_multi_select_value(
    value: Any, *, options: Sequence[str] | None = None
) -> list[str]:
    """Normalize a MULTI_SELECT payload as a list of strings."""
    if value is None:
        raise ValueError("Value cannot be None for MULTI_SELECT fields")
    if not isinstance(value, list | tuple | set):
        raise ValueError("MULTI_SELECT values must be provided as a list of strings")
    coerced: list[str] = [str(item) for item in value]
    if options:
        invalid = [item for item in coerced if item not in options]
        if invalid:
            raise ValueError(
                f"Value(s) {', '.join(invalid)} are not in the available options"
            )
    return coerced
