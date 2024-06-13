"""Extract emails using regex."""

import itertools
import re
from typing import Annotated

import polars as pl

from tracecat.registry import Field, registry

EMAIL_REGEX = r"\b[A-Za-z0-9._%+-]+@[A-Za-z0-9.-]+\.[A-Z|a-z]{2,}\b"


def pl_extract_emails(texts: pl.Expr) -> pl.Expr:
    """Extract emails from a column of strings in Polars."""
    return texts.str.extract_all(EMAIL_REGEX)


@registry.register(
    description="Extract unique emails from a list of strings.",
    namespace="core.extraction",
    default_title="Email Extractor",
    display_group="Data Extraction",
)
def extract_emails(
    texts: Annotated[
        list[str], Field(..., description="The list of strings to extract emails from")
    ],
) -> list[str]:
    """Extract unique emails from a list of strings."""
    emails = itertools.chain.from_iterable(
        re.findall(EMAIL_REGEX, text) for text in texts
    )
    return list(set(emails))
