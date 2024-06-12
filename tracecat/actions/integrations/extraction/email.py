"""Extract emails using regex."""

import polars as pl

EMAIL_REGEX = r"\b[A-Za-z0-9._%+-]+@[A-Za-z0-9.-]+\.[A-Z|a-z]{2,}\b"


def extract_emails(texts: pl.Expr) -> pl.Expr:
    """Extract emails from a column of strings."""
    return texts.str.extract_all(EMAIL_REGEX)
