import re

# ASN (Autonomous System Number)
ASN_REGEX = r"\bAS\d+\b"


def extract_asns(text: str) -> list[str]:
    """Extract Autonomous System Numbers, e.g. AS1234, from a string."""
    return list(set(re.findall(ASN_REGEX, text)))
