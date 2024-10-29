"""Extract emails using regex."""

import re
from typing import Annotated

from pydantic import Field

from tracecat_registry import registry

EMAIL_REGEX = r"[a-zA-Z0-9._%+-]+@[a-zA-Z0-9.-]+\.[a-zA-Z]{2,}"


def normalize_email_address(email: str) -> str:
    """Convert sub-addressed email to a normalized email address."""
    local_part, domain = email.split("@")
    local_part = local_part.split("+")[0]
    return f"{local_part}@{domain}"


@registry.register(
    default_title="Extract emails",
    description="Extract unique emails from a list of strings.",
    namespace="etl.extraction",
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
    emails = set()
    for text in texts:
        emails.update(re.findall(EMAIL_REGEX, text))
    if normalize and len(emails) > 0:
        emails = {normalize_email_address(email) for email in emails}
    return list(emails)
