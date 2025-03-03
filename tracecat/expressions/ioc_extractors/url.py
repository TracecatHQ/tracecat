"""Functions for extracting URLs from a string.

Supports extracting from: `http`, `https`, `tcp`, `udp`, `ftp`, `sftp`, `ftps` protocols
and defanged versions of the above URLs.


Defang variations:
- Square brackets: replace `.` with `[.]` (e.g. https://example[.].com)
- Parentheses: replace `.` with `(.)` (e.g. https://example(.).com)
- Escaped dot: replace `.` with `\\.`  (e.g. https://example\\.com)
- Protocol substitution: `hxxp://` instead of `http://`
- Protocol bracket variations: `http[:]//` or `http(:)//`
- Slash replacement: `http:[/][/]` or `http:(/)(/)`
- Complete protocol masking: `xxxx://example.com`, `xxx://example.com`
"""

import functools
import re

from pydantic import AnyHttpUrl, AnyUrl, BaseModel, ValidationError

# URL regexes
# Match only HTTP and HTTPS URLs
HTTP_URL_REGEX = r"(?:https?):\/\/(?:www\.)?[-a-zA-Z0-9@:%._\+~#=\u00A0-\uFFFF]{1,256}(?:\.[a-zA-Z0-9()\u00A0-\uFFFF]{1,63})+(?::\d{1,5})?(?:\/[-a-zA-Z0-9()@:%_\+.~#?&\/=\u00A0-\uFFFF]*)?(?<![?&=/#.])"

# Match URLs with all supported protocols (http, https, tcp, udp, ftp, sftp, ftps)
URL_REGEX = r"(?:https?|tcp|udp|ftp|sftp|ftps):\/\/(?:www\.)?[-a-zA-Z0-9@:%._\+~#=\u00A0-\uFFFF]{1,256}(?:\.[a-zA-Z0-9()\u00A0-\uFFFF]{1,63})+(?::\d{1,5})?(?:\/[-a-zA-Z0-9()@:%_\+.~#?&\/=\u00A0-\uFFFF]*)?(?<![?&=/#.])"


class UrlModel(BaseModel):
    url: AnyUrl


class HttpUrlModel(BaseModel):
    url: AnyHttpUrl


def is_url(url: str) -> bool:
    """Check if a string is a valid URL."""
    try:
        UrlModel(url=url)  # type: ignore
        return True
    except ValidationError:
        return False


def is_http_url(url: str) -> bool:
    """Check if a string is a valid HTTP/HTTPS URL."""
    try:
        HttpUrlModel(url=url)  # type: ignore
        return True
    except ValidationError:
        return False


def extract_urls(
    text: str, http_only: bool = False, include_defanged: bool = False
) -> list[str]:
    """Extract URLs from text, optionally including defanged ones."""

    regex_pattern = HTTP_URL_REGEX if http_only else URL_REGEX
    validate_url = is_http_url if http_only else is_url

    # Extract all potential URLs
    matched_urls = re.findall(regex_pattern, text)

    if include_defanged:
        # Normalize the text
        replacements = {
            # Domain defanging
            "[.]": ".",
            "(.)": ".",
            "[dot]": ".",
            "(dot)": ".",
            " dot ": ".",
            " colon ": ":",
            # Protocol defanging
            "hxxp://": "http://",
            "hxxps://": "https://",
            "xxp://": "http://",
            "xxps://": "https://",
            "xxxp://": "http://",
            "xxxps://": "https://",
            "http[:]//": "http://",
            "https[:]//": "https://",
            "http(:)//": "http://",
            "https(:)//": "https://",
            "http:[/][/]": "http://",
            "https:[/][/]": "https://",
            "http:(/)(/)": "http://",
            "https:(/)(/)": "https://",
        }
        normalized_text = functools.reduce(
            lambda substring, replacement: substring.replace(
                replacement[0], replacement[1]
            ),
            replacements.items(),
            text,
        )
        matched_normalized_urls = re.findall(regex_pattern, normalized_text)
        matched_urls.extend(matched_normalized_urls)

    unique_urls = list({url for url in matched_urls if validate_url(url)})
    return unique_urls
