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


def normalize_email_address(email: str) -> str:
    """Convert sub-addressed email to a normalized email address."""
    local_part, domain = email.split("@")
    local_part = local_part.split("+")[0]
    return f"{local_part}@{domain}"


@registry.register(
    description="Extract unique emails from a list of strings.",
    namespace="integrations.extraction",
    default_title="Email Extractor",
    display_group="Data Extraction",
)
def extract_emails(
    texts: Annotated[
        list[str], Field(..., description="The list of strings to extract emails from")
    ],
    normalize: Annotated[
        bool,
        Field(
            default=False,
            description="Whether to normalize emails by removing sub-addresses",
        ),
    ] = False,
) -> list[str]:
    """Extract unique emails from a list of strings."""
    emails = itertools.chain.from_iterable(
        re.findall(EMAIL_REGEX, text) for text in texts
    )
    if normalize:
        normalized_emails = [
            normalize_email_address(email) for email in extract_emails(texts)
        ]
        emails += normalized_emails
    return list(set(emails))
