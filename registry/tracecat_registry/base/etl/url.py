import itertools
import re
from typing import Annotated

from pydantic import Field

from tracecat_registry import registry

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
        str | list[str],
        Field(..., description="Text or list of text to extract URLs from"),
    ],
) -> list[str]:
    """Extract unique URLs from a list of strings."""
    if isinstance(texts, str):
        texts = [texts]

    urls = itertools.chain.from_iterable(re.findall(URL_REGEX, text) for text in texts)
    unique_urls = set(urls)
    return list(unique_urls)
