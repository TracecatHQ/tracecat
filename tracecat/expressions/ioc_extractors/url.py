import re

from pydantic import BaseModel, HttpUrl, ValidationError

# URL
# Match URLs including paths and query parameters
URL_REGEX = r"https?:\/\/(?:www\.)?[-a-zA-Z0-9@:%._\+~#=]{1,256}\.[a-zA-Z0-9()]{1,6}\b(?:[-a-zA-Z0-9()@:%_\+.~#?&\/=]*)(?<![?&=/#.])"


class UrlModel(BaseModel):
    url: HttpUrl


def extract_urls(text: str) -> list[str]:
    """Extract unique URLs from a string."""
    # Use a set to deduplicate URLs
    url_matches = set(re.findall(URL_REGEX, text))
    result = []
    for url in url_matches:
        try:
            # Validate with pydantic but preserve original format
            UrlModel(url=url)
            result.append(url)
        except ValidationError:
            pass
    return result
