from typing import Any

import polars as pl
from pydantic import BaseModel

from tracecat.tables.enums import SqlType


class InferredColumn(BaseModel):
    """Information about an inferred column."""

    name: str
    type: SqlType
    sample_value: Any = None


class SchemaInferenceService:
    """Service for inferring table schema from sample data."""

    def __init__(self, sample_data: dict[str, Any] = None):
        """
        Initialize the schema inference service.

        Args:
            sample_data: Optional initial sample data to infer from
        """
        self.sample_data = sample_data
        self._inferred_columns: list[InferredColumn] | None = None

        if sample_data:
            self._infer_schema()

    def _infer_schema(self) -> None:
        """Infer schema from current sample data."""
        if not self.sample_data:
            return

        try:
            # Create a Polars DataFrame with the sample data
            df = pl.DataFrame([self.sample_data])

            self._inferred_columns = []
            for col_name in df.columns:
                # Get the Polars dtype
                polars_type = df.schema[col_name]
                sql_type = self._map_polars_type(polars_type)

                self._inferred_columns.append(
                    InferredColumn(
                        name=col_name,
                        type=sql_type,
                        sample_value=self.sample_data[col_name],
                    )
                )
        except Exception:
            # Fallback to basic type detection if Polars fails
            self._inferred_columns = self._fallback_type_inference()

    def _map_polars_type(self, polars_type: Any) -> SqlType:
        """Map a Polars data type to SQL type."""
        # Mapping dictionary
        type_mapping = {
            pl.Utf8: SqlType.TEXT,
            pl.Int64: SqlType.INTEGER,
            pl.Float64: SqlType.DECIMAL,
            pl.Boolean: SqlType.BOOLEAN,
            pl.Struct: SqlType.JSONB,
            pl.Object: SqlType.JSONB,
        }

        # Try direct type matching
        for pl_type, sql_type in type_mapping.items():
            if isinstance(polars_type, pl_type):
                return sql_type

        # Try to handle numeric types
        type_str = str(polars_type)
        if any(int_type in type_str for int_type in ["Int", "UInt"]):
            return SqlType.INTEGER
        if "Float" in type_str:
            return SqlType.DECIMAL

        # Default to TEXT
        return SqlType.TEXT

    def get_inferred_columns(self) -> list[InferredColumn]:
        """
        Get the inferred columns.

        Returns:
            List of inferred columns with their types
        """
        if self._inferred_columns is None:
            self._infer_schema()

        return self._inferred_columns or []
