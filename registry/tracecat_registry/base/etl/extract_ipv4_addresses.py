from typing import Annotated
from pydantic import Field
from tracecat_registry import registry
from .extract_ip_addresses import extract_ipv4_addresses  # Import de la fonction

@registry.register(
    default_title="Extract IPv4 addresses",
    description="Extract unique IPv4 addresses from a list of strings.",
    namespace="etl.extraction",
    display_group="Data Extraction",
)
def extract_ipv4(
    texts: Annotated[
        str | list[str],
        Field(..., description="Text or list of text to extract IPv4 addresses from"),
    ],
) -> list[str]:
    """Extraction des adresses IPv4."""
    return extract_ipv4_addresses(texts)
