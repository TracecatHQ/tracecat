import pytest

from tracecat.actions.integrations.extraction import (
    extract_emails,
    extract_ip_addresses,
)


@pytest.mark.parametrize(
    "texts, expected_ips",
    [
        (
            [
                "This is a sample text with IPv4 address 192.168.0.1 and IPv6 address 2001:0db8:85a3:0000:0000:8a2e:0370:7334."
            ],
            ["192.168.0.1", "2001:db8:85a3::8a2e:370:7334"],
        ),
        (
            ["Another line with IPv4 10.0.0.1 and invalid IP 999.999.999.999."],
            ["10.0.0.1"],
        ),
        (["IPv6 example: fe80::1ff:fe23:4567:890a"], ["fe80::1ff:fe23:4567:890a"]),
        (
            [
                "Some more texts with IPv4 172.16.254.1 and IPv6 2001:0db8:0000:0042:0000:8a2e:0370:7334."
            ],
            ["172.16.254.1", "2001:db8:0:42:0:0:8a2e:370:7334"],
        ),
        (
            [
                '{"ip": "192.168.1.1", "more_info": {"ipv6": "2001:0db8:85a3:0000:0000:8a2e:0370:7334"}}'
            ],
            ["192.168.1.1", "2001:db8:85a3::8a2e:370:7334"],
        ),
        (["IPv6 with mixed notation: 2001:db8::1:0:0:1"], ["2001:db8::1:0:0:1"]),
        (["IPv4-mapped IPv6 address: ::ffff:192.0.2.128"], ["::ffff:192.0.2.128"]),
    ],
    ids=[
        "mixed_ips",
        "ipv4_and_invalid_ip",
        "single_ipv6",
        "multiple_ips",
        "json_with_ips",
        "ipv6_mixed_notation",
        "ipv4_mapped_ipv6",
    ],
)
def test_extract_ip_address(texts, expected_ips):
    extracted_ips = extract_ip_addresses(texts)
    assert sorted(extracted_ips) == sorted(expected_ips)


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


if __name__ == "__main__":
    pytest.main()
