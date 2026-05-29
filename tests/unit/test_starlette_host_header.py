from starlette.requests import Request


def test_malformed_host_header_does_not_poison_request_url_path() -> None:
    """Regression coverage for CVE-2026-48710 / GHSA-86qp-5c8j-p5mr.

    Starlette should not let invalid Host header path/query delimiters alter
    ``request.url.path``. Route dispatch uses ``scope["path"]``; security checks
    that inspect ``request.url.path`` must see the same path.
    """
    scope = {
        "type": "http",
        "method": "GET",
        "scheme": "https",
        "server": ("api.internal", 8000),
        "root_path": "",
        "path": "/api/private/resource",
        "raw_path": b"/api/private/resource",
        "query_string": b"",
        "headers": [(b"host", b"example.com/health?ignored=")],
        "client": ("203.0.113.10", 50000),
    }

    request = Request(scope)

    assert request.url.path == scope["path"]
