from pathlib import Path
from typing import Any

import pytest
import yaml

from tracecat.expressions.ioc_extractors import (
    extract_asns,
    extract_cves,
    extract_domains,
    extract_emails,
    extract_ipv4,
    extract_ipv6,
    extract_mac,
    extract_md5,
    extract_sha1,
    extract_sha256,
    extract_sha512,
    extract_urls,
    normalize_email,
)

### Utilities


@pytest.mark.parametrize(
    "email,expected",
    [
        ("user+info@example.com", "user@example.com"),
        ("john.doe+newsletter@example.com", "john.doe@example.com"),
        ("regular@example.com", "regular@example.com"),
        ("test+multiple+plus@example.com", "test@example.com"),
        ("first.last+tag@domain.co.uk", "first.last@domain.co.uk"),
        ("user+123@sub.example.org", "user@sub.example.org"),
        ("MIXED+case@Example.COM", "mixed@example.com"),
        ("dot.ted+suffix@example.com", "dot.ted@example.com"),
    ],
)
def test_normalize_email(email, expected):
    assert normalize_email(email) == expected


### IOC EXTRACTORS


def load_test_data(ioc_type: str) -> list[Any]:
    """Load IoC test data from a YAML file in the tests/data/iocs directory.

    Returns a list of pytest.param objects, each with a "text", "expected" and "id" key.
    """
    base_path = Path(__file__).parent.parent.joinpath("data/iocs")
    path = base_path.joinpath(f"{ioc_type}.yml").resolve()
    with path.open("r") as f:
        test_cases = yaml.safe_load(f)
    return [
        pytest.param(test_case["text"], test_case["expected"], id=test_case["id"])
        for test_case in test_cases
    ]


@pytest.mark.parametrize("text,expected", load_test_data("asn"))
def test_extract_asns(text: str, expected: list[str]) -> None:
    extracted = extract_asns(text)
    assert sorted(extracted) == sorted(expected)


@pytest.mark.parametrize("text,expected", load_test_data("cve"))
def test_extract_cves(text: str, expected: list[str]) -> None:
    extracted = extract_cves(text)
    assert sorted(extracted) == sorted(expected)


@pytest.mark.parametrize("text,expected", load_test_data("domain"))
def test_extract_domains(text: str, expected: list[str]) -> None:
    extracted = extract_domains(text)
    assert sorted(extracted) == sorted(expected)


@pytest.mark.parametrize("text,expected", load_test_data("md5"))
def test_extract_md5(text: str, expected: list[str]) -> None:
    extracted = extract_md5(text)
    assert sorted([h.lower() for h in extracted]) == sorted(
        [h.lower() for h in expected]
    )


@pytest.mark.parametrize("text,expected", load_test_data("sha1"))
def test_extract_sha1(text: str, expected: list[str]) -> None:
    extracted = extract_sha1(text)
    assert sorted([h.lower() for h in extracted]) == sorted(
        [h.lower() for h in expected]
    )


@pytest.mark.parametrize("text,expected", load_test_data("sha256"))
def test_extract_sha256(text: str, expected: list[str]) -> None:
    extracted = extract_sha256(text)
    assert sorted(extracted) == sorted(expected)


@pytest.mark.parametrize("text,expected", load_test_data("sha512"))
def test_extract_sha512(text: str, expected: list[str]) -> None:
    extracted = extract_sha512(text)
    assert sorted(extracted) == sorted(expected)


@pytest.mark.parametrize("text,expected", load_test_data("mac"))
def test_extract_mac_addresses(text: str, expected: list[str]) -> None:
    extracted = extract_mac(text)
    assert sorted([m.upper() for m in extracted]) == sorted(
        [m.upper() for m in expected]
    )


@pytest.mark.parametrize("text,expected", load_test_data("email"))
def test_extract_emails(text, expected):
    extracted = extract_emails(text=text, normalize=False)
    assert sorted(extracted) == sorted(expected)


@pytest.mark.parametrize("text,expected", load_test_data("ipv4"))
def test_extract_ipv4_addresses(text, expected):
    assert sorted(extract_ipv4(text=text)) == sorted(expected)


@pytest.mark.parametrize("text,expected", load_test_data("ipv6"))
def test_extract_ipv6_addresses(text, expected):
    assert sorted(extract_ipv6(text=text)) == sorted(expected)


@pytest.mark.parametrize("text,expected", load_test_data("url_any"))
def test_extract_urls(text, expected):
    extracted_urls = extract_urls(text=text)
    assert sorted(extracted_urls) == sorted(expected)


@pytest.mark.parametrize("text,expected", load_test_data("url_http"))
def test_extract_urls_http_only(text, expected):
    assert sorted(extract_urls(text=text, http_only=True)) == sorted(expected)


### IOC EXTRACTORS VALIDATION BYPASS


def test_extract_domains_exception():
    """Test that extract_domains ignores invalid domains."""
    mixed_input = """
    Valid: example.com, sub.example.org
    Invalid: not_a_domain, .invalid, example..com, -example.com
    """
    result = extract_domains(mixed_input)
    assert sorted(result) == ["example.com", "sub.example.org"]


def test_extract_urls_exception():
    """Test that extract_urls ignores invalid URLs."""
    mixed_input = """
    Valid: https://example.com, http://sub.example.org/path, ftp://example.com, http://example.com
    Invalid: http://.com, https://invalid, http://example.com:abc
    """
    result = extract_urls(mixed_input)
    assert sorted(result) == [
        "ftp://example.com",
        "http://example.com",
        "http://sub.example.org/path",
        "https://example.com",
    ]


def test_extract_ipv4_addresses_exception():
    """Test that extract_ipv4_addresses ignores invalid IPv4 addresses."""
    mixed_input = """
    Valid: 192.168.1.1, 10.0.0.1, 8.8.8.8
    Invalid: 256.256.256.256, 192.168.1, 192.168.1.1.1, 999.999.999.999
    """
    result = extract_ipv4(mixed_input)
    assert sorted(result) == ["10.0.0.1", "192.168.1.1", "8.8.8.8"]


def test_extract_ipv6_addresses_exception():
    """Test that extract_ipv6_addresses ignores invalid IPv6 addresses."""
    test_input = "Valid IPv6: 2001:db8::1 and 2001:0db8:85a3:0000:0000:8a2e:0370:7334\nInvalid: xyz"
    result = extract_ipv6(test_input)
    expected = ["2001:db8::1", "2001:0db8:85a3:0000:0000:8a2e:0370:7334"]
    assert sorted(result) == sorted(expected)


def test_extract_mac_addresses_exception():
    """Test that extract_mac_addresses ignores invalid MAC addresses."""
    mixed_input = """
    Valid: 00:11:22:33:44:55, AA-BB-CC-DD-EE-FF
    Invalid: 00:11:22:33:44, 00:11:22:33:44:55:66, GG:HH:II:JJ:KK:LL
    """
    result = extract_mac(mixed_input)
    assert sorted(result) == ["00:11:22:33:44:55", "AA:BB:CC:DD:EE:FF"]


def test_extract_emails_exception():
    """Test that extract_emails ignores invalid emails."""
    mixed_input = """
    Valid: user@example.com, first.last@sub.domain.com
    Invalid: user@, @example.com, user@domain, plaintext
    """
    result = extract_emails(mixed_input)
    assert sorted(result) == ["first.last@sub.domain.com", "user@example.com"]
