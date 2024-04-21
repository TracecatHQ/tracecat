import pytest

from tracecat.integrations.virustotal import (
    get_domain_report,
    get_file_report,
    get_ip_address_report,
    get_url_report,
)


@pytest.mark.parametrize(
    "file_hash",
    # Mirai hash
    ["10c796b7308ac0b9c38f1caa95c798b2b28c46adaa037a9c3a9ebdd3569824e3"],
)
def test_get_file_report(file_hash):
    """Check expected attributes in File object."""
    result = get_file_report(file_hash)
    assert result["sha256"] == file_hash
    required_keys = [
        "sandbox_verdicts",
        "reputation",
        "last_analysis_results",
        "last_analysis_stats",
    ]
    assert all(
        key in result for key in required_keys
    ), "Some keys are missing in the file report"


def test_get_url_report():
    """Check expected attributes in URL object."""
    url = "http://example.com"
    result = get_url_report(url)
    assert result["url"] == url
    required_keys = [
        "title",
        "last_analysis_results",
        "last_analysis_stats",
        "total_votes",
        "reputation",
    ]
    assert all(
        key in result for key in required_keys
    ), "Some keys are missing in the URL report"


def test_get_domain_report():
    domain = "ycombinator.com"
    result = get_domain_report(domain)
    assert result["domain"] == domain
    required_keys = [
        "title",
        "last_analysis_results",
        "last_analysis_stats",
        "total_votes",
    ]
    assert all(
        key in result for key in required_keys
    ), "Some keys are missing in the domain report"


def test_get_ip_address_report():
    ip = "8.8.8.8"  # Google's DNS
    result = get_ip_address_report(ip)
    assert result["id"] == ip
    required_keys = ["title", "regional_internet_registry", "whois"]
    assert all(
        key in result for key in required_keys
    ), "Some keys are missing in the IP address report"
