import pytest

from tracecat.integrations.urlscan import analyze_url


@pytest.mark.integration
def test_analyze_url_live():
    test_url = "http://google.com"
    result = analyze_url(test_url)

    # Assert that key interesting fields are present
    assert all(
        key in result["verdicts"]["urlscan"]
        for key in ["score", "categories", "brands"]
    )
    assert all(
        key in result["page"] for key in ["city", "country", "ip", "asnname", "asn"]
    )
