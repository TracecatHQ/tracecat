import io
import json
from typing import Any

import polars as pl
from pydantic import BaseModel

from tracecat.logger import logger
from tracecat.tables.enums import SqlType


class InferredColumn(BaseModel):
    """Information about an inferred column."""

    name: str
    type: SqlType
    sample_values: list[Any] = []


class SchemaTypeInference:
    """Service for inferring table schema from CSV files."""

    def __init__(self, file_content: bytes):
        """
        Initialize the schema inference service.

        Args:
            file_content: CSV file content as bytes
        """
        self.file_content = file_content
        self.df = None
        self._inferred_columns: list[InferredColumn] | None = None
        self._infer_schema_from_file()

    def _infer_schema_from_file(self) -> None:
        """Infer schema from CSV file content."""
        try:
            # Create a StringIO object from the content
            csv_file = io.StringIO(self.file_content.decode("utf-8"))

            # Use Polars to read and infer schema from the CSV file
            self.df = pl.read_csv(
                csv_file,
                infer_schema_length=None,  # Use all rows for inference
                n_rows=None,  # Read all rows
            )

            # Extract sample values (up to 5 rows) for display
            num_samples = min(5, len(self.df))

            self._inferred_columns = []
            for col_name in self.df.columns:
                # Get the Polars dtype
                polars_type = self.df.schema[col_name]
                sql_type = self._map_polars_type(polars_type, col_name)

                sample_values = []
                for i in range(num_samples):
                    value = self.df[i, col_name]
                    sample_values.append(value)

                self._inferred_columns.append(
                    InferredColumn(
                        name=col_name, type=sql_type, sample_values=sample_values
                    )
                )
        except Exception as e:
            logger.error(f"Error inferring schema from CSV: {str(e)}")
            # Fall back to empty schema
            self._inferred_columns = []

    def is_valid_json(self, s: str) -> bool:
        """Check if a string is valid JSON."""
        try:
            json.loads(s)
            return True
        except json.JSONDecodeError:
            return False

    def _map_polars_type(self, polars_type: Any, col_name: str) -> SqlType:
        """Map a Polars data type to SQL type."""
        # Mapping dictionary
        type_mapping = {
            pl.Utf8: SqlType.TEXT,
            pl.Int8: SqlType.INTEGER,
            pl.Int16: SqlType.INTEGER,
            pl.Int32: SqlType.INTEGER,
            pl.Int64: SqlType.INTEGER,
            pl.UInt8: SqlType.INTEGER,
            pl.UInt16: SqlType.INTEGER,
            pl.UInt32: SqlType.INTEGER,
            pl.UInt64: SqlType.INTEGER,
            pl.Float32: SqlType.DECIMAL,
            pl.Float64: SqlType.DECIMAL,
            pl.Boolean: SqlType.BOOLEAN,
            pl.Struct: SqlType.JSONB,
            pl.Object: SqlType.JSONB,
            pl.Date: SqlType.TEXT,
            pl.Time: SqlType.TEXT,
        }

        if isinstance(polars_type, pl.Utf8):
            if len(self.df) > 0:
                first_value = self.df[0, col_name]
                if isinstance(first_value, str) and self.is_valid_json(first_value):
                    return SqlType.JSONB

        # Try direct type matching
        for pl_type, sql_type in type_mapping.items():
            if isinstance(polars_type, pl_type):
                return sql_type
        # Default to TEXT
        return SqlType.TEXT

    def get_inferred_columns(self) -> list[InferredColumn]:
        """
        Get the inferred columns.

        Returns:
            List of inferred columns with their types and sample values
        """
        return self._inferred_columns or []
