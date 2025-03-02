import re

# HASH
MD5_REGEX = r"\b[a-fA-F0-9]{32}\b"
SHA1_REGEX = r"\b[a-fA-F0-9]{40}\b"
SHA256_REGEX = r"\b[a-fA-F0-9]{64}\b"
SHA512_REGEX = r"\b[a-fA-F0-9]{128}\b"


def extract_md5_hashes(text: str) -> list[str]:
    """Extract MD5 hashes from a string."""
    # MD5 doesn't need validation beyond the regex, but we still ensure uniqueness
    return list(set(re.findall(MD5_REGEX, text)))


def extract_sha1_hashes(text: str) -> list[str]:
    """Extract SHA1 hashes from a string."""
    # SHA1 doesn't need validation beyond the regex, but we still ensure uniqueness
    return list(set(re.findall(SHA1_REGEX, text)))


def extract_sha256_hashes(text: str) -> list[str]:
    """Extract SHA256 hashes from a string."""
    # SHA256 doesn't need validation beyond the regex, but we still ensure uniqueness
    return list(set(re.findall(SHA256_REGEX, text)))


def extract_sha512_hashes(text: str) -> list[str]:
    """Extract SHA512 hashes from a string."""
    # SHA512 doesn't need validation beyond the regex, but we still ensure uniqueness
    return list(set(re.findall(SHA512_REGEX, text)))
