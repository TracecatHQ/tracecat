"""IoC extractors with validation using Pydantic.

References:
- https://docs.iocparser.com/
"""

import ipaddress
import re

from pydantic import BaseModel, EmailStr, FilePath, HttpUrl
from pydantic_extra_types.domain import DomainStr
from pydantic_extra_types.mac_address import MacAddress
from pydantic_extra_types.phone_numbers import PhoneNumber

# ASN (Autonomous System Number)
ASN_REGEX = r"\bAS\d+\b"


def extract_asns(text: str) -> list[str]:
    """Extract Autonomous System Numbers, e.g. AS1234, from a string."""
    return re.findall(ASN_REGEX, text)


# DOMAIN
# This regex aims to match domain names while avoiding matching URLs
DOMAIN_REGEX = r"(?<![:/\w])(?:(?:[a-zA-Z0-9](?:[a-zA-Z0-9-]{0,61}[a-zA-Z0-9])?\.)+[a-zA-Z]{2,})(?![:/\w])"


class DomainModel(BaseModel):
    domain: DomainStr


def extract_domains(text: str) -> list[str]:
    """Extract domain names, e.g. example.com, from a string."""
    return [
        DomainModel(domain=domain).domain for domain in re.findall(DOMAIN_REGEX, text)
    ]


# URL
# Match URLs including paths and query parameters
URL_REGEX = r"https?:\/\/(?:www\.)?[-a-zA-Z0-9@:%._\+~#=]{1,256}\.[a-zA-Z0-9()]{1,6}\b(?:[-a-zA-Z0-9()@:%_\+.~#?&\/=]*)(?<![?&=/#.])"


class UrlModel(BaseModel):
    url: HttpUrl


def extract_urls(text: str) -> list[str]:
    """Extract unique URLs from a string."""
    return [
        UrlModel(url=url).url.unicode_string() for url in re.findall(URL_REGEX, text)
    ]


# IP ADDRESS
IPV4_REGEX = r"\b(?:[0-9]{1,3}\.){3}[0-9]{1,3}\b"
IPV6_REGEX = r"\b(?:[0-9a-fA-F]{1,4}:){7}[0-9a-fA-F]{1,4}\b"


def extract_ipv4_addresses(text: str) -> list[str]:
    """Extract unique IPv4 addresses from a string."""
    return [ip for ip in re.findall(IPV4_REGEX, text) if ipaddress.IPv4Address(ip)]


def extract_ipv6_addresses(text: str) -> list[str]:
    """Extract unique IPv6 addresses from a string."""
    return [ip for ip in re.findall(IPV6_REGEX, text) if ipaddress.IPv6Address(ip)]


def extract_ip_addresses(text: str) -> list[str]:
    """Extract unique IPv4 and IPv6 addresses from a string."""
    ipv4_addrs = extract_ipv4_addresses(text)
    ipv6_addrs = extract_ipv6_addresses(text)
    return ipv4_addrs + ipv6_addrs


# MAC ADDRESS
MAC_REGEX = r"\b(?:[0-9A-Fa-f]{2}[:-]){5}[0-9A-Fa-f]{2}\b"


class MacAddressModel(BaseModel):
    mac_address: MacAddress


def extract_mac_addresses(text: str) -> list[str]:
    """Extract MAC addresses from a string.

    Examples: 00:11:22:33:44:55, 00-11-22-33-44-55
    """
    return [
        MacAddressModel(mac_address=mac).mac_address
        for mac in re.findall(MAC_REGEX, text)
    ]


# EMAIL
EMAIL_REGEX = r"[a-zA-Z0-9._%+-]+@[a-zA-Z0-9.-]+\.[a-zA-Z]{2,}"


class EmailModel(BaseModel):
    email: EmailStr


def normalize_email(email: str) -> str:
    """Convert sub-addressed email to a normalized email address."""
    local_part, domain = email.split("@")
    local_part = local_part.split("+")[0]
    return f"{local_part}@{domain}"


def extract_emails(text: str, normalize: bool = False) -> list[str]:
    """Extract unique emails from a string."""
    # Find all potential email matches
    potential_emails = re.findall(EMAIL_REGEX, text)
    validated_emails = {EmailModel(email=email).email for email in potential_emails}
    if normalize:
        validated_emails = {normalize_email(email) for email in validated_emails}
    return list(validated_emails)


# HASH
MD5_REGEX = r"\b[a-fA-F0-9]{32}\b"
SHA1_REGEX = r"\b[a-fA-F0-9]{40}\b"
SHA256_REGEX = r"\b[a-fA-F0-9]{64}\b"


def extract_md5_hashes(text: str) -> list[str]:
    """Extract MD5 hashes from a string."""
    return re.findall(MD5_REGEX, text)


def extract_sha1_hashes(text: str) -> list[str]:
    """Extract SHA1 hashes from a string."""
    return re.findall(SHA1_REGEX, text)


def extract_sha256_hashes(text: str) -> list[str]:
    """Extract SHA256 hashes from a string."""
    return re.findall(SHA256_REGEX, text)


# FILE PATH
WINDOWS_PATH_REGEX = r'C:\\(?:[^\\/:*?"<>|\r\n]+\\)*[^\\/:*?"<>|\r\n]*'
UNIX_PATH_REGEX = r"/(?:[^/\0<>:\"\\|?*\r\n]+/)*[^/\0<>:\"\\|?*\r\n]*"
MACOS_PATH_REGEX = r"~/(?:[^/\0<>:\"\\|?*\r\n]+/)*[^/\0<>:\"\\|?*\r\n]*"


class FilePathModel(BaseModel):
    file_path: FilePath


def extract_windows_file_paths(text: str) -> list[str]:
    """Extract Windows file paths from a string."""
    return [
        FilePathModel(file_path=file_path).file_path.as_posix()
        for file_path in re.findall(WINDOWS_PATH_REGEX, text)
    ]


def extract_unix_file_paths(text: str) -> list[str]:
    """Extract Unix file paths from a string."""
    return [
        FilePathModel(file_path=file_path).file_path.as_posix()
        for file_path in re.findall(UNIX_PATH_REGEX, text)
    ]


def extract_macos_file_paths(text: str) -> list[str]:
    """Extract macOS file paths from a string."""
    return [
        FilePathModel(file_path=file_path).file_path.as_posix()
        for file_path in re.findall(MACOS_PATH_REGEX, text)
    ]


def extract_file_paths(text: str) -> list[str]:
    """Extract file paths from a string."""
    windows_file_paths = extract_windows_file_paths(text)
    unix_file_paths = extract_unix_file_paths(text)
    macos_file_paths = extract_macos_file_paths(text)
    return windows_file_paths + unix_file_paths + macos_file_paths


# CVE (Common Vulnerabilities and Exposures)
CVE_REGEX = r"CVE-\d{4}-\d{4,7}"


def extract_cves(text: str) -> list[str]:
    """Extract CVE IDs, such as CVE-2021-34527, from a string."""
    return re.findall(CVE_REGEX, text)


# PHONE NUMBER
PHONE_REGEX = (
    r"(?:\+?\d{1,4}[-.\s]?)?(?:\(?\d{1,4}\)?[-.\s]?)?(?:\d{1,4}[-.\s]?){1,3}\d{1,4}"
)


class PhoneNumberModel(BaseModel):
    phone_number: PhoneNumber


def _clean_phone_number(phone: str) -> str:
    """Clean a phone number for validation.

    - Removes extension if present
    - Strips whitespace and common separators
    - Preserves existing country code if present
    - For numbers without country code, tries to determine format:
      - North American numbers (typically 10 digits)
      - International numbers (based on length and patterns)

    Returns a cleaned string suitable for PhoneNumber validation.
    """

    # Remove extension if present
    if " ext" in phone.lower():
        phone = phone.split(" ext")[0]

    # Strip all special characters
    cleaned = re.sub(r"[^0-9+]", "", phone)

    # If it already has a plus sign, just keep it as is
    if cleaned.startswith("+"):
        return cleaned

    # Handle North American numbers (10 digits)
    if re.match(r"^1?\d{10}$", cleaned):
        if cleaned.startswith("1") and len(cleaned) == 11:
            return "+" + cleaned
        else:
            return "+1" + cleaned

    # For other formats, try to make educated guesses based on length and patterns
    # For example, many European numbers are 8-12 digits without country code
    # UK mobile starts with 07, but internationally it's +447...
    if cleaned.startswith("0") and len(cleaned) >= 10:
        # This might be a European-style number with a leading 0
        # Strip the leading 0 and add the country code
        if len(cleaned) == 11 and cleaned.startswith("07"):  # UK mobile
            return "+44" + cleaned[1:]
        elif len(cleaned) == 10 and cleaned.startswith("06"):  # French mobile
            return "+33" + cleaned[1:]
        # Add more country-specific patterns as needed

    # Default fallback - assume North American if we can't determine
    return "+1" + cleaned


def extract_phone_numbers(text: str) -> list[str]:
    """Extract phone numbers from a string."""
    return [
        phone
        for phone in re.findall(PHONE_REGEX, text)
        if PhoneNumberModel(phone_number=phone)
    ]
