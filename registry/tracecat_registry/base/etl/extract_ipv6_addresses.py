from typing import Annotated
from pydantic import Field
from tracecat_registry import registry
from .extract_ip_addresses import extract_ipv6_addresses  # Import de la fonction

@registry.register(
    default_title="Extract IPv6 addresses",
    description="Extract unique IPv6 addresses from a list of strings.",
    namespace="etl.extraction",
    display_group="Data Extraction",
)
def extract_ipv6(
    texts: Annotated[
        str | list[str],
        Field(..., description="Text or list of text to extract IPv6 addresses from"),
    ],
) -> list[str]:
    """Extraction des adresses IPv6."""
    return extract_ipv6_addresses(texts)
