"""Functions for extracting IPv4 and IPv6 addresses from a string.

Supports extracting from regular IPv4 and IPv6 addresses,
as well as the following defanged variants:

IPv4:
- Full brackets: [1.1.1.1]
- Brackets-dot: 1[.]1[.]1[.]1
- Parentheses-dot: 1(.)1(.)1(.)1
- Escaped dot: 1\\.1\\.1\\.1
- Text-bracket-dot: 1[dot]1[dot]1[dot]1
- Space-dot-space: 1 dot 1 dot 1 dot 1

IPv6:
- Full brackets: [2001:db8::1]
- Brackets-colon: 2001[:]db8[:]85a3[:]8d3[:]1319[:]8a2e[:]370[:]7348
- Parentheses-colon: 2001(:)db8(:)85a3(:)8d3(:)1319(:)8a2e(:)370(:)7348
- Escaped colon: 2001\\:db8\\:85a3\\:8d3\\:1319\\:8a2e\\:370\\:7348
- Text-bracket-colon: 2001[colon]db8[colon]85a3[colon]8d3[colon]1319[colon]8a2e[colon]370[colon]7348
- Space-colon-space: 2001 colon db8 colon 85a3 colon 8d3 colon 1319 colon 8a2e colon 370 colon 7348
"""

import ipaddress
import itertools
import re
from collections.abc import Iterator
from enum import Enum, auto
from functools import lru_cache
from ipaddress import AddressValueError

# IP ADDRESS
IPV4_REGEX = r"\b(?:[0-9]{1,3}\.){3}[0-9]{1,3}\b"

# Different defanged IPv4 formats:
# 1. With square brackets: 192[.]168[.]1[.]1
# 2. With parentheses: 192(.)168(.)1(.)1
# 3. With escaped dots: 192\.168\.1\.1
# 4. With full brackets: [192.168.1.1]
# 5. With dot replaced by "dot": 192[dot]168[dot]1[dot]1
IPV4_DEFANGED_REGEX = (
    # [.] format
    r"\b(?:[0-9]{1,3}\[\.\]){3}[0-9]{1,3}\b|"
    # (.) format
    r"\b(?:[0-9]{1,3}\(\.\)){3}[0-9]{1,3}\b|"
    # \. format
    r"\b(?:[0-9]{1,3}\\\.){3}[0-9]{1,3}\b|"
    # [IP] format (full brackets)
    r"\[(?:[0-9]{1,3}\.){3}[0-9]{1,3}\]|"
    # [dot] format
    r"\b(?:[0-9]{1,3}\[dot\]){3}[0-9]{1,3}\b|"
    # space dot space format
    r"\b[0-9]{1,3} dot [0-9]{1,3} dot [0-9]{1,3} dot [0-9]{1,3}\b"
)

# Comprehensive IPv6 regex that covers:
# 1. Full format: 2001:0db8:85a3:0000:0000:8a2e:0370:7334
# 2. Compressed format: 2001:db8::1
# 3. Bracketed format: [2001:db8::1]
# 4. Includes uppercase and lowercase hex digits
IPV6_REGEX = (
    # Full format IPv6 without brackets
    r"(?:\b(?:[0-9A-Fa-f]{1,4}:){7}[0-9A-Fa-f]{1,4}\b)"
    # Compressed IPv6 format without brackets (with :: notation)
    r"|(?:\b(?:[0-9A-Fa-f]{1,4}:){0,6}(?:[0-9A-Fa-f]{1,4})?::"
    r"(?:[0-9A-Fa-f]{1,4}:){0,6}[0-9A-Fa-f]{1,4}?\b)"
    # Full format IPv6 with brackets
    r"|(?:\[(?:[0-9A-Fa-f]{1,4}:){7}[0-9A-Fa-f]{1,4}\])"
    # Compressed IPv6 format with brackets (with :: notation)
    r"|(?:\[(?:[0-9A-Fa-f]{1,4}:){0,6}(?:[0-9A-Fa-f]{1,4})?::"
    r"(?:[0-9A-Fa-f]{1,4}:){0,6}[0-9A-Fa-f]{1,4}?\])"
)

# Different defanged IPv6 formats
IPV6_DEFANGED_REGEX = (
    # [:] format for each colon
    r"\b(?:[0-9A-Fa-f]{1,4}\[\:\]){1,7}[0-9A-Fa-f]{1,4}\b|"
    # [:] format with compressed notation
    r"\b(?:[0-9A-Fa-f]{1,4}\[\:\]){0,6}(?:[0-9A-Fa-f]{1,4})?\[\:\]\[\:\]"
    r"(?:[0-9A-Fa-f]{1,4}\[\:\]){0,6}[0-9A-Fa-f]{1,4}\b|"
    # (:) format for each colon
    r"\b(?:[0-9A-Fa-f]{1,4}\(\:\)){1,7}[0-9A-Fa-f]{1,4}\b|"
    # (:) format with compressed notation
    r"\b(?:[0-9A-Fa-f]{1,4}\(\:\)){0,6}(?:[0-9A-Fa-f]{1,4})?\(\:\)\(\:\)"
    r"(?:[0-9A-Fa-f]{1,4}\(\:\)){0,6}[0-9A-Fa-f]{1,4}\b|"
    # \: format for each colon
    r"\b(?:[0-9A-Fa-f]{1,4}\\:){1,7}[0-9A-Fa-f]{1,4}\b|"
    # \: format with compressed notation
    r"\b(?:[0-9A-Fa-f]{1,4}\\:){0,6}(?:[0-9A-Fa-f]{1,4})?\\:\\:"
    r"(?:[0-9A-Fa-f]{1,4}\\:){0,6}[0-9A-Fa-f]{1,4}\b|"
    # [colon] format
    r"\b(?:[0-9A-Fa-f]{1,4}\[colon\]){1,7}[0-9A-Fa-f]{1,4}\b|"
    # space colon space format
    r"\b[0-9A-Fa-f]{1,4}( colon [0-9A-Fa-f]{1,4}){1,7}\b|"
    # space colon colon space format (compressed)
    r"\b[0-9A-Fa-f]{1,4}( colon [0-9A-Fa-f]{1,4}){0,6} colon colon( [0-9A-Fa-f]{1,4}){1,7}\b"
)


# Define IP types enum
class IPType(Enum):
    """Enum for IP address types."""

    IPV4 = auto()
    IPV6 = auto()


# Define defang patterns enum
class DefangPattern(Enum):
    """Enum for defanged IP patterns."""

    # IPv4 patterns
    IPV4_BRACKET = auto()  # [x.x.x.x]
    IPV4_BRACKET_DOT = auto()  # x[.]x[.]x[.]x
    IPV4_PAREN_DOT = auto()  # x(.)x(.)x(.)x
    IPV4_ESCAPED_DOT = auto()  # x\.x\.x\.x
    IPV4_BRACKET_WORD = auto()  # x[dot]x[dot]x[dot]x
    IPV4_SPACE_DOT = auto()  # x dot x dot x dot x

    # IPv6 patterns
    IPV6_BRACKET = auto()  # [x:x:x:x:x:x:x:x]
    IPV6_BRACKET_COLON = auto()  # x[:]x[:]x[:]x[:]x[:]x[:]x[:]x
    IPV6_PAREN_COLON = auto()  # x(:)x(:)x(:)x(:)x(:)x(:)x(:)x
    IPV6_ESCAPED_COLON = auto()  # x\:x\:x\:x\:x\:x\:x\:x
    IPV6_BRACKET_WORD = auto()  # x[colon]x[colon]x[colon]x[colon]x
    IPV6_SPACE_COLON = auto()  # x colon x colon x colon x colon x


# Compile regular expressions for better performance
REGEX_IPV4 = re.compile(IPV4_REGEX)
REGEX_IPV4_BRACKET = re.compile(r"\[(\d{1,3}\.\d{1,3}\.\d{1,3}\.\d{1,3})\]")
REGEX_IPV4_BRACKET_DOT = re.compile(
    r"(\d{1,3})\[\.\](\d{1,3})\[\.\](\d{1,3})\[\.\](\d{1,3})"
)
REGEX_IPV4_PAREN_DOT = re.compile(
    r"(\d{1,3})\(\.\)(\d{1,3})\(\.\)(\d{1,3})\(\.\)(\d{1,3})"
)
REGEX_IPV4_ESCAPED_DOT_YAML = re.compile(
    r"(\d{1,3})\\\\\.(\d{1,3})\\\\\.(\d{1,3})\\\\\.(\d{1,3})"
)
REGEX_IPV4_ESCAPED_DOT = re.compile(r"(\d{1,3})\\\.(\d{1,3})\\\.(\d{1,3})\\\.(\d{1,3})")
REGEX_IPV4_BRACKET_WORD_DOT = re.compile(
    r"(\d{1,3})\[dot\](\d{1,3})\[dot\](\d{1,3})\[dot\](\d{1,3})"
)
REGEX_IPV4_SPACE_DOT = re.compile(
    r"(\d{1,3}) dot (\d{1,3}) dot (\d{1,3}) dot (\d{1,3})"
)

REGEX_IPV6 = re.compile(IPV6_REGEX)
REGEX_IPV6_WORD_PATTERN = re.compile(r"(\w+) colon (\w+) colon colon (\w+)")

# Compile more specific IPv6 regex patterns for different defanging styles
REGEX_IPV6_BRACKET = re.compile(r"\[([0-9A-Fa-f:]+)\]")  # [2001:db8::1]
REGEX_IPV6_BRACKET_COLON = re.compile(
    r"([0-9A-Fa-f]{1,4})(?:\[\:\]([0-9A-Fa-f]{1,4})){1,7}"
)  # 2001[:]db8[:]...
REGEX_IPV6_BRACKET_COLON_COMPRESSED = re.compile(
    r"([0-9A-Fa-f]{1,4})(?:\[\:\]([0-9A-Fa-f]{1,4})){0,6}\[\:\]\[\:\]([0-9A-Fa-f]{1,4})(?:\[\:\]([0-9A-Fa-f]{1,4})){0,6}"
)  # with [:][:]
REGEX_IPV6_PAREN_COLON = re.compile(
    r"([0-9A-Fa-f]{1,4})(?:\(\:\)([0-9A-Fa-f]{1,4})){1,7}"
)  # 2001(:)db8(:)...
REGEX_IPV6_PAREN_COLON_COMPRESSED = re.compile(
    r"([0-9A-Fa-f]{1,4})(?:\(\:\)([0-9A-Fa-f]{1,4})){0,6}\(\:\)\(\:\)([0-9A-Fa-f]{1,4})(?:\(\:\)([0-9A-Fa-f]{1,4})){0,6}"
)  # with (:)(:)
REGEX_IPV6_ESCAPED_COLON = re.compile(
    r"([0-9A-Fa-f]{1,4})(?:\\:([0-9A-Fa-f]{1,4})){1,7}"
)  # 2001\:db8\:...
REGEX_IPV6_ESCAPED_COLON_COMPRESSED = re.compile(
    r"([0-9A-Fa-f]{1,4})(?:\\:([0-9A-Fa-f]{1,4})){0,6}\\:\\:([0-9A-Fa-f]{1,4})(?:\\:([0-9A-Fa-f]{1,4})){0,6}"
)  # with \:\:
REGEX_IPV6_BRACKET_WORD = re.compile(
    r"([0-9A-Fa-f]{1,4})(?:\[colon\]([0-9A-Fa-f]{1,4})){1,7}"
)  # 2001[colon]db8[colon]...
REGEX_IPV6_SPACE_COLON = re.compile(
    r"([0-9A-Fa-f]{1,4})( colon ([0-9A-Fa-f]{1,4})){1,7}"
)  # 2001 colon db8 colon ...
REGEX_IPV6_SPACE_COLON_COMPRESSED = re.compile(
    r"([0-9A-Fa-f]{1,4})( colon ([0-9A-Fa-f]{1,4})){0,6} colon colon( ([0-9A-Fa-f]{1,4})){0,6}"
)  # with colon colon

# IPv6 defang substitution patterns - we'll remove this since we won't need normalization
# IPV6_DEFANG_SUBSTITUTIONS = [
#     (re.compile(r'\[\:\]'), ':'),
#     (re.compile(r'\(\:\)'), ':'),
#     (re.compile(r'\\\\:'), ':'),
#     (re.compile(r'\\:'), ':'),
#     (re.compile(r'\[colon\]'), ':'),
#     (re.compile(r' colon '), ':'),
#     (re.compile(r' colon colon '), '::'),
#     (re.compile(r': colon :'), '::'),
# ]

# Pattern definitions for match-case usage
IPV4_PATTERNS = {
    DefangPattern.IPV4_BRACKET: REGEX_IPV4_BRACKET,
    DefangPattern.IPV4_BRACKET_DOT: REGEX_IPV4_BRACKET_DOT,
    DefangPattern.IPV4_PAREN_DOT: REGEX_IPV4_PAREN_DOT,
    DefangPattern.IPV4_ESCAPED_DOT: REGEX_IPV4_ESCAPED_DOT,
    DefangPattern.IPV4_BRACKET_WORD: REGEX_IPV4_BRACKET_WORD_DOT,
    DefangPattern.IPV4_SPACE_DOT: REGEX_IPV4_SPACE_DOT,
}

# Add IPv6 patterns dictionary similar to IPv4
IPV6_PATTERNS = {
    DefangPattern.IPV6_BRACKET: REGEX_IPV6_BRACKET,
    DefangPattern.IPV6_BRACKET_COLON: REGEX_IPV6_BRACKET_COLON,
    DefangPattern.IPV6_PAREN_COLON: REGEX_IPV6_PAREN_COLON,
    DefangPattern.IPV6_ESCAPED_COLON: REGEX_IPV6_ESCAPED_COLON,
    DefangPattern.IPV6_BRACKET_WORD: REGEX_IPV6_BRACKET_WORD,
    DefangPattern.IPV6_SPACE_COLON: REGEX_IPV6_SPACE_COLON,
}


@lru_cache(maxsize=1024)
def is_ipv4(ip: str) -> bool:
    """Check if a string is a valid IPv4 address. Results are cached for performance."""
    try:
        ipaddress.IPv4Address(ip)
        return True
    except (AddressValueError, ValueError):
        return False


@lru_cache(maxsize=1024)
def is_ipv6(ip: str) -> bool:
    """Check if a string is a valid IPv6 address. Results are cached for performance."""
    try:
        ipaddress.IPv6Address(ip)
        return True
    except (AddressValueError, ValueError):
        return False


def is_ip(ip: str) -> bool:
    """Check if a string is a valid IP address (either IPv4 or IPv6)."""
    return is_ipv4(ip) or is_ipv6(ip)


def extract_ipv4(text: str, include_defanged: bool = False) -> list[str]:
    """Extract unique IPv4 addresses from a string. Includes defanged variants as an option."""

    def _validate_ipv4(ip_string: str) -> str | None:
        """Validate an IPv4 address and return it if valid, None otherwise."""
        try:
            ipaddress.IPv4Address(ip_string)
            return ip_string
        except (AddressValueError, ValueError):
            return None

    def _extract_regular_ipv4() -> Iterator[str]:
        """Extract regular IPv4 addresses from the text."""
        for ip in REGEX_IPV4.findall(text):
            validated = _validate_ipv4(ip)
            if validated:
                yield validated

    def _process_ipv4_defang_pattern(pattern_type: DefangPattern) -> Iterator[str]:
        """Process a specific IPv4 defang pattern and yield valid IPs."""
        pattern = IPV4_PATTERNS.get(pattern_type)
        if not pattern:
            return

        for match in pattern.finditer(text):
            if pattern_type in (
                DefangPattern.IPV4_BRACKET_DOT,
                DefangPattern.IPV4_PAREN_DOT,
                DefangPattern.IPV4_ESCAPED_DOT,
                DefangPattern.IPV4_BRACKET_WORD,
            ):
                # For patterns like x[.]x[.]x[.]x, x(.)x(.)x(.)x, x\.x\.x\.x, x[dot]x[dot]x[dot]x
                ip = ".".join([match.group(i) for i in range(1, 5)])
            elif pattern_type == DefangPattern.IPV4_BRACKET:
                # For pattern like [x.x.x.x]
                ip = match.group(1)
            elif pattern_type == DefangPattern.IPV4_SPACE_DOT:
                # For pattern like "x dot x dot x dot x"
                ip = ".".join([match.group(i) for i in range(1, 5)])
            else:
                continue

            validated = _validate_ipv4(ip)
            if validated:
                yield validated

    # Begin with regular IPv4 addresses
    ip_generators = [_extract_regular_ipv4()]

    # Process defanged IPv4 addresses if requested
    if include_defanged:
        # Add generators for each defang pattern
        defang_generators = [
            _process_ipv4_defang_pattern(pattern_type) for pattern_type in IPV4_PATTERNS
        ]
        ip_generators.extend(defang_generators)

    # Combine all generators and deduplicate
    return list(set(itertools.chain.from_iterable(ip_generators)))


def extract_ipv6(text: str, include_defanged: bool = False) -> list[str]:
    """Extract unique IPv6 addresses from a string. Includes defanged variants as an option."""

    def _validate_ipv6(ip_string: str) -> str | None:
        """Validate an IPv6 address and return it if valid, None otherwise."""
        try:
            ipaddress.IPv6Address(ip_string)
            return ip_string
        except (AddressValueError, ValueError):
            return None

    def _extract_regular_ipv6() -> Iterator[str]:
        """Extract regular IPv6 addresses from the text."""
        for match in REGEX_IPV6.finditer(text):
            ip_str = match.group(0)
            if ip_str.startswith("[") and ip_str.endswith("]"):
                ip_str = ip_str[1:-1]
            validated = _validate_ipv6(ip_str)
            if validated:
                yield validated

    def _process_ipv6_defang_pattern(pattern_type: DefangPattern) -> Iterator[str]:
        """Process a specific IPv6 defang pattern and yield valid IPs."""
        pattern = IPV6_PATTERNS.get(pattern_type)
        if not pattern:
            return

        # Handle different patterns with appropriate transformations
        if pattern_type == DefangPattern.IPV6_BRACKET:
            # For pattern like [x:x:x:x:x:x:x:x]
            for match in pattern.finditer(text):
                ip = match.group(1)
                validated = _validate_ipv6(ip)
                if validated:
                    yield validated

        elif pattern_type in (
            DefangPattern.IPV6_BRACKET_COLON,
            DefangPattern.IPV6_PAREN_COLON,
            DefangPattern.IPV6_ESCAPED_COLON,
            DefangPattern.IPV6_BRACKET_WORD,
        ):
            # For patterns where colons are defanged: 2001[:]db8[:] etc.
            # This is a simplification - real implementation would need to handle
            # more complex pattern matching specific to each format
            for full_match in pattern.finditer(text):
                # Extract all parts and join with colons
                match_text = full_match.group(0)
                if pattern_type == DefangPattern.IPV6_BRACKET_COLON:
                    parts = match_text.replace("[:]", ":").split(":")
                elif pattern_type == DefangPattern.IPV6_PAREN_COLON:
                    parts = match_text.replace("(:", ":").replace(")", "").split(":")
                elif pattern_type == DefangPattern.IPV6_ESCAPED_COLON:
                    parts = match_text.replace("\\:", ":").split(":")
                elif pattern_type == DefangPattern.IPV6_BRACKET_WORD:
                    parts = match_text.replace("[colon]", ":").split(":")

                # Join parts and validate
                try:
                    ip = ":".join([p for p in parts if p])
                    validated = _validate_ipv6(ip)
                    if validated:
                        yield validated
                except (AddressValueError, ValueError, IndexError):
                    pass

        elif pattern_type == DefangPattern.IPV6_SPACE_COLON:
            # For pattern like "x colon x colon x"
            for match_text in re.finditer(
                r"\b[0-9A-Fa-f]{1,4}( colon [0-9A-Fa-f]{1,4}){1,7}\b", text
            ):
                try:
                    ip = match_text.group(0).replace(" colon ", ":")
                    validated = _validate_ipv6(ip)
                    if validated:
                        yield validated
                except (AddressValueError, ValueError):
                    pass

    # We'll also need to handle specific cases like compressed notation
    def _process_ipv6_special_cases() -> Iterator[str]:
        """Process special cases that don't fit the standard pattern processing."""

        # Handle compressed notations with double colons
        patterns = [
            (REGEX_IPV6_BRACKET_COLON_COMPRESSED, "[:]", ":"),
            (REGEX_IPV6_PAREN_COLON_COMPRESSED, "(:)", ":"),
            (REGEX_IPV6_ESCAPED_COLON_COMPRESSED, "\\:", ":"),
            (REGEX_IPV6_SPACE_COLON_COMPRESSED, " colon ", ":"),
        ]

        for pattern, search, _ in patterns:
            for match in pattern.finditer(text):
                try:
                    # Create a normalized string with proper :: compression
                    match_text = match.group(0)
                    if search == " colon ":
                        ip = match_text.replace(" colon colon ", "::").replace(
                            " colon ", ":"
                        )
                    else:
                        double_search = search + search
                        ip = match_text.replace(double_search, "::").replace(
                            search, ":"
                        )

                    validated = _validate_ipv6(ip)
                    if validated:
                        yield validated
                except (AddressValueError, ValueError):
                    pass

        # Handle specific word pattern like "2001 colon db8 colon colon 1"
        for match in REGEX_IPV6_WORD_PATTERN.finditer(text):
            try:
                ip_str = f"{match.group(1)}:{match.group(2)}::{match.group(3)}"
                validated = _validate_ipv6(ip_str)
                if validated:
                    yield validated
            except (AddressValueError, ValueError):
                pass

    # Begin with regular IPv6 addresses
    ip_generators = [_extract_regular_ipv6()]

    # Process defanged IPv6 addresses if requested
    if include_defanged:
        # Add generators for each defang pattern
        defang_generators = [
            _process_ipv6_defang_pattern(pattern_type) for pattern_type in IPV6_PATTERNS
        ]
        ip_generators.extend(defang_generators)

        # Add special cases generator
        ip_generators.append(_process_ipv6_special_cases())

    # Combine all generators and deduplicate
    return list(set(itertools.chain.from_iterable(ip_generators)))


def extract_ip(text: str, include_defanged: bool = False) -> list[str]:
    """Extract unique IPv4 and IPv6 addresses from a string. Includes defanged variants as an option."""
    ipv4_addrs = extract_ipv4(text, include_defanged)
    ipv6_addrs = extract_ipv6(text, include_defanged)
    return ipv4_addrs + ipv6_addrs
