"""Functions for extracting domain names from a string.

Defanged variants:
- Square brackets: replace `.` with `[.]` (e.g. example[.].com)
- Parentheses: replace `.` with `(.)` (e.g. example(.).com)
- Escaped dot: replace `.` with `\\.`  (e.g. example\\.com)
"""

import functools
import re

from pydantic import TypeAdapter, ValidationError
from pydantic_extra_types.domain import DomainStr

# DOMAIN
# This regex aims to match domains (even within URLs)
DOMAIN_REGEX = re.compile(
    r"""
    # Negative lookbehind - don't match if preceded by :, /, or word char
    (?<![:/\w])
    # Domain part - match at least one label + dot followed by TLD
    (?>                                  # Atomic group to prevent backtracking
        (?>                              # Atomic group for the repeating label + dot sequence
            (?:
                xn--[a-zA-Z0-9]+         # Punycode (IDN) label
                |                        # OR
                [a-zA-Z0-9]              # Regular domain label
                (?>[a-zA-Z0-9-]{0,61}    # Up to 61 alphanumeric or hyphen chars
                [a-zA-Z0-9])?            # Ending with alphanumeric (optional group)
            )
            \.                           # Followed by a dot
        )+                               # One or more label + dot sequences

        # TLD part - handle both regular and punycode TLDs
        (?:
            xn--[a-zA-Z0-9-]+            # Punycode TLD
            |                            # OR
            [a-zA-Z]{2,}                 # Regular TLD (at least 2 alpha chars)
        )
        # Optional additional TLDs (e.g., .co.uk)
        (?:\.
            (?:
                xn--[a-zA-Z0-9-]+        # Punycode TLD
                |                        # OR
                [a-zA-Z]{2,}             # Regular TLD
            )
        )*                               # Zero or more additional TLD components
    )
    # Negative lookahead - don't match if followed by :, /, or word char
    (?![:/\w])
""",
    re.VERBOSE,
)

DomainTypeAdapter = TypeAdapter(DomainStr)


def is_domain(domain: str) -> bool:
    """Check if a string is a valid domain name."""
    try:
        DomainTypeAdapter.validate_python(domain)
        return True
    except ValidationError:
        return False


def extract_domains(text: str, include_defanged: bool = False) -> list[str]:
    """Extract domain names from a string."""
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
