import pytest
from tracecat_registry.base.etl.email import extract_emails
from tracecat_registry.base.etl.ip_address import extract_ipv4_addresses
from tracecat_registry.base.etl.url import extract_urls


@pytest.mark.parametrize(
    "texts, expected_emails, normalize",
    [
        (
            ["Contact us at support@example.com or sales@example.org."],
            ["support@example.com", "sales@example.org"],
            False,
        ),
        (
            ["Invalid email test@.com and correct email info@example.net"],
            ["info@example.net"],
            False,
        ),
        (
            ["Email with subaddress john.doe+newsletter@example.com"],
            ["john.doe+newsletter@example.com"],
            False,
        ),
        (
            [
                "Email with subaddress john.doe+newsletter@example.com",
                "Another email jane.smith+promo@example.com",
            ],
            ["john.doe@example.com", "jane.smith@example.com"],
            True,
        ),
        (
            [
                '{"email": "user@example.com", "more_info": {"contact": "admin@sub.example.com"}}'
            ],
            ["user@example.com", "admin@sub.example.com"],
            False,
        ),
        (
            [
                '{"email": "user+info@example.com", "more_info": {"contact": "admin+test@sub.example.com"}}'
            ],
            ["user@example.com", "admin@sub.example.com"],
            True,
        ),
    ],
    ids=[
        "valid_emails",
        "invalid_and_valid_email",
        "subaddressed_email",
        "normalized_subaddressed_emails",
        "json_with_emails",
        "json_with_normalized_emails",
    ],
)
def test_extract_emails(texts, expected_emails, normalize):
    extracted_emails = extract_emails(texts=texts, normalize=normalize)
    assert sorted(extracted_emails) == sorted(expected_emails)


@pytest.mark.parametrize(
    "texts, expected_ip_addresses",
    [
        (
            [
                "IPv4 address 192.168.1.1 and some random text",
                "Another IPv4 address 10.0.0.1 in the mix",
            ],
            ["192.168.1.1", "10.0.0.1"],
        ),
        (["No IP addresses here"], []),
        (
            [
                "Invalid IPv4 address 999.999.999.999 should not match",
                "Another invalid 256.256.256.256 address",
            ],
            [],
        ),
        (
            [
                '{"alert": {"context": {"ip_address": "192.168.1.1", "description": "Suspicious activity detected"}}',
                '{"event": {"source": {"ip_address": "10.0.0.1", "port": 8080}, "destination": {"ip_address": "172.16.0.1", "port": 443}}}',
            ],
            ["192.168.1.1", "10.0.0.1", "172.16.0.1"],
        ),
        (
            [
                "Multiple addresses: 192.168.1.1, 10.0.0.1",
                "More addresses: 172.16.0.1, 192.168.0.1",
            ],
            ["192.168.1.1", "10.0.0.1", "172.16.0.1", "192.168.0.1"],
        ),
    ],
    ids=[
        "valid_ipv4",
        "no_ip_addresses",
        "invalid_ip_addresses",
        "json_with_ip_addresses",
        "multiple_valid_addresses",
    ],
)
def test_extract_ipv4_addresses(texts, expected_ip_addresses):
    assert sorted(extract_ipv4_addresses(texts=texts)) == sorted(expected_ip_addresses)


@pytest.mark.parametrize(
    "texts, expected_urls",
    [
        (
            ["Visit our website at https://example.com for more info."],
            ["https://example.com"],
        ),
        (
            [
                "Check out https://example.com/path and http://example.org/another-path for details."
            ],
            ["https://example.com/path", "http://example.org/another-path"],
        ),
        (
            ["Invalid URL test http://.com and correct URL https://example.net/path"],
            ["https://example.net/path"],
        ),
        (
            [
                "Multiple URLs: https://example.com/path1, http://example.org/path2, and https://sub.example.net/path3."
            ],
            [
                "https://example.com/path1",
                "http://example.org/path2",
                "https://sub.example.net/path3",
            ],
        ),
        (
            [
                '{"url": "https://example.com/path", "more_info": {"link": "http://sub.example.com/another-path"}}'
            ],
            ["https://example.com/path", "http://sub.example.com/another-path"],
        ),
        (
            ["Text with no URLs should return an empty list."],
            [],
        ),
    ],
    ids=[
        "single_url",
        "multiple_urls_with_paths",
        "invalid_and_valid_url_with_path",
        "multiple_urls_in_text_with_paths",
        "json_with_urls_and_paths",
        "no_urls",
    ],
)
def test_extract_urls(texts, expected_urls):
    extracted_urls = extract_urls(texts=texts)
    assert sorted(extracted_urls) == sorted(expected_urls)
