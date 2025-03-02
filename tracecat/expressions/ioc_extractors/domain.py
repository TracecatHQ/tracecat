import re

from pydantic import BaseModel, ValidationError
from pydantic_extra_types.domain import DomainStr

# DOMAIN
# This regex aims to match domain names while avoiding matching URLs
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


def extract_domains(text: str) -> list[str]:
    """Extract domain names, e.g. example.com, from a string."""
    unique_domains = set()
    for domain in re.findall(DOMAIN_REGEX, text):
        try:
            validated_domain = DomainModel(domain=domain).domain  # type: ignore
            unique_domains.add(validated_domain)
        except ValidationError:
            pass
    return list(unique_domains)
