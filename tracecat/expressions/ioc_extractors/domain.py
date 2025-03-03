"""Functions for extracting domain names from a string.

Defanged variants:
- Square brackets: replace `.` with `[.]` (e.g. example[.].com)
- Parentheses: replace `.` with `(.)` (e.g. example(.).com)
- Escaped dot: replace `.` with `\\.`  (e.g. example\\.com)
"""

import functools
import re

from pydantic import BaseModel, ValidationError
from pydantic_extra_types.domain import DomainStr

# DOMAIN
# This regex aims to match domains (even within URLs)
DOMAIN_REGEX = r"(?<![:/\w])(?:(?:xn--[a-zA-Z0-9]+|[a-zA-Z0-9](?:[a-zA-Z0-9-]{0,61}[a-zA-Z0-9])?\.)+[a-zA-Z]{2,}(?:\.[a-zA-Z]{2,})*)(?![:/\w])"


class DomainModel(BaseModel):
    domain: DomainStr


def is_domain(domain: str) -> bool:
    """Check if a string is a valid domain name."""
    try:
        DomainModel(domain=domain)  # type: ignore
        return True
    except ValidationError:
        return False


def extract_domains(text: str, include_defanged: bool = False) -> list[str]:
    """Extract domain names, e.g. example.com, from a string.

    Args:
        text: The text to extract domains from.
        include_defanged: Whether to include defanged domains.

    Returns:
        A list of extracted domain names.
    """
    matched_domains = re.findall(DOMAIN_REGEX, text)
    if include_defanged:
        # Normalize the text to handle defanged domains
        replacements = {
            # Domain defanging
            "[.]": ".",
            "(.)": ".",
            "[dot]": ".",
            "(dot)": ".",
            " dot ": ".",
            r"\.": ".",  # Handle escaped dots
        }
        normalized_text = functools.reduce(
            lambda substring, replacement: substring.replace(
                replacement[0], replacement[1]
            ),
            replacements.items(),
            text,
        )
        matched_normalized_domains = re.findall(DOMAIN_REGEX, normalized_text)
        matched_domains.extend(matched_normalized_domains)
    return list({domain for domain in matched_domains if is_domain(domain)})
