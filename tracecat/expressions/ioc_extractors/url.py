import re

from pydantic import AnyUrl, BaseModel, ValidationError

# URL
# Match URLs including paths, query parameters, ports, and IDNs with multiple protocol support
URL_REGEX = r"(?:https?|tcp|udp|ftp|sftp|ftps):\/\/(?:www\.)?[-a-zA-Z0-9@:%._\+~#=\u00A0-\uFFFF]{1,256}(?:\.[a-zA-Z0-9()\u00A0-\uFFFF]{1,63})+(?::\d{1,5})?(?:\/[-a-zA-Z0-9()@:%_\+.~#?&\/=\u00A0-\uFFFF]*)?(?<![?&=/#.])"


class UrlModel(BaseModel):
    url: AnyUrl


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
