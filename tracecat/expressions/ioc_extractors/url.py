"""Functions for extracting URLs from a string.

Supported protocols:
- `http`, `https`
- `tcp`, `udp`
- `ftp`, `sftp`, `ftps`

Defanged variants:
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

from pydantic import AnyHttpUrl, AnyUrl, TypeAdapter, ValidationError

# URL regexes
# Non-ASCII hosts and paths, minus U+00A0, U+2000-U+206F (spaces, curly quotes,
# ellipsis), U+3000-U+3002 (ideographic space and punctuation) and U+FEFF,
# which end a URL in running text.
_NON_ASCII = r"\u00A1-\u1FFF\u2070-\u2FFF\u3003-\uFEFE\uFF00-\uFFFF"
# Paths also keep dashes U+2010-U+2015, as in Wikipedia titles like `1914–1918`.
# Hosts don't, so prose like `example.com—next` can't extend the domain.
_PATH_NON_ASCII = rf"{_NON_ASCII}\u2010-\u2015"

# Match only HTTP and HTTPS URLs
HTTP_URL_REGEX = rf"(?:https?):\/\/(?:www\.)?[-a-zA-Z0-9@:%._\+~#={_NON_ASCII}]{{1,256}}(?:\.[a-zA-Z0-9(){_NON_ASCII}]{{1,63}})+(?::\d{{1,5}})?(?:\/[-a-zA-Z0-9()@:%_\+.~#?&\/={_PATH_NON_ASCII}]*)?(?<![?&=/#.])"

# Match URLs with all supported protocols (http, https, tcp, udp, ftp, sftp, ftps)
URL_REGEX = rf"(?:https?|tcp|udp|ftp|sftp|ftps):\/\/(?:www\.)?[-a-zA-Z0-9@:%._\+~#={_NON_ASCII}]{{1,256}}(?:\.[a-zA-Z0-9(){_NON_ASCII}]{{1,63}})+(?::\d{{1,5}})?(?:\/[-a-zA-Z0-9()@:%_\+.~#?&\/={_PATH_NON_ASCII}]*)?(?<![?&=/#.])"


# URL type adapters

UrlTypeAdapter = TypeAdapter(AnyUrl)
HttpUrlTypeAdapter = TypeAdapter(AnyHttpUrl)


def is_url(url: str) -> bool:
    """Check if a string is a valid URL."""
    try:
        UrlTypeAdapter.validate_python(url)
        return True
    except ValidationError:
        return False


def is_http_url(url: str) -> bool:
    """Check if a string is a valid HTTP/HTTPS URL."""
    try:
        HttpUrlTypeAdapter.validate_python(url)
        return True
    except ValidationError:
        return False


_TRAILING_PUNCTUATION = "?&=/#."


def _trim_unbalanced_parens(url: str) -> str:
    """Drop trailing `)` that close a parenthesis opened before the URL.

    Keeps balanced ones such as `https://en.wikipedia.org/wiki/Mars_(planet)`.
    """
    unmatched = url.count(")") - url.count("(")
    end = len(url)
    while end and unmatched > 0:
        if url[end - 1] == ")":
            unmatched -= 1
        elif url[end - 1] not in _TRAILING_PUNCTUATION:
            break
        end -= 1
    return url[:end].rstrip(_TRAILING_PUNCTUATION)


def extract_urls(
    text: str, http_only: bool = False, include_defanged: bool = False
) -> list[str]:
    """Extract URLs from text, optionally including defanged ones."""

    _regex_pattern = HTTP_URL_REGEX if http_only else URL_REGEX
    _is_url = is_http_url if http_only else is_url

    # Extract all potential URLs
    matched_urls = re.findall(_regex_pattern, text)

    if include_defanged:
        # Normalize the text
        replacements = {
            # Domain defanging
            "[.]": ".",
            "(.)": ".",
            "\\.": ".",
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
        matched_normalized_urls = re.findall(_regex_pattern, normalized_text)
        matched_urls.extend(matched_normalized_urls)

    trimmed_urls = (_trim_unbalanced_parens(url) for url in matched_urls)
    unique_urls = list({url for url in trimmed_urls if _is_url(url)})
    return unique_urls
