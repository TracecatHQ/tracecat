import itertools
import re
from typing import Annotated

from tracecat.registry import Field, registry

# Improved regular expression to match URLs including paths and query parameters
URL_REGEX = r'https?://(?:[a-zA-Z0-9-]+\.)+[a-zA-Z]{2,6}(?:/[^\s"\'\],\.]*)?'


@registry.register(
    default_title="Extract URLs",
    description="Extract unique URLs from a list of strings.",
    namespace="etl.extraction",
    display_group="Data Extraction",
)
def extract_urls(
    texts: Annotated[
        list[str],
        Field(..., description="The list of strings to extract URLs from"),
    ],
) -> list[str]:
    """Extract unique URLs from a list of strings."""
    urls = itertools.chain.from_iterable(re.findall(URL_REGEX, text) for text in texts)
    unique_urls = set(urls)
    return list(unique_urls)
