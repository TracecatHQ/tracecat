import re

from pydantic import AnyHttpUrl, AnyUrl, BaseModel, ValidationError

# URL regexes
# Match only HTTP and HTTPS URLs
HTTP_URL_REGEX = r"(?:https?):\/\/(?:www\.)?[-a-zA-Z0-9@:%._\+~#=\u00A0-\uFFFF]{1,256}(?:\.[a-zA-Z0-9()\u00A0-\uFFFF]{1,63})+(?::\d{1,5})?(?:\/[-a-zA-Z0-9()@:%_\+.~#?&\/=\u00A0-\uFFFF]*)?(?<![?&=/#.])"

# Match URLs with all supported protocols (http, https, tcp, udp, ftp, sftp, ftps)
URL_REGEX = r"(?:https?|tcp|udp|ftp|sftp|ftps):\/\/(?:www\.)?[-a-zA-Z0-9@:%._\+~#=\u00A0-\uFFFF]{1,256}(?:\.[a-zA-Z0-9()\u00A0-\uFFFF]{1,63})+(?::\d{1,5})?(?:\/[-a-zA-Z0-9()@:%_\+.~#?&\/=\u00A0-\uFFFF]*)?(?<![?&=/#.])"


class UrlModel(BaseModel):
    url: AnyUrl


def extract_urls(text: str, http_only: bool = False) -> list[str]:
    """Extract unique URLs from a string."""
    regex_pattern = HTTP_URL_REGEX if http_only else URL_REGEX
    validator = AnyHttpUrl if http_only else AnyUrl

    url_matches = set(re.findall(regex_pattern, text))
    result = []

    for url in url_matches:
        try:
            validator(url=url)
            result.append(url)
        except ValidationError:
            pass

    return result
