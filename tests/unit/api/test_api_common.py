import pytest
from starlette.requests import Request

from tracecat.api.common import generic_exception_handler, tracecat_exception_handler
from tracecat.exceptions import TracecatException
from tracecat.observability.sentry import REDACTED_VALUE


def _make_request(query_string: bytes, path: str = "/boom") -> Request:
    return Request(
        {
            "type": "http",
            "method": "GET",
            "path": path,
            "query_string": query_string,
            "headers": [],
            "server": ("testserver", 80),
            "scheme": "http",
            "client": ("testclient", 50000),
        }
    )


@pytest.mark.anyio
async def test_generic_exception_handler_logs_with_exception(mocker):
    logger = mocker.patch("tracecat.api.common.logger")
    exc = RuntimeError("boom")
    request = _make_request(b"foo=bar")

    response = await generic_exception_handler(request, exc)

    logger.exception.assert_called_once()
    logger.error.assert_not_called()
    args, kwargs = logger.exception.call_args
    assert args == ("Unexpected error",)
    assert kwargs["exc"] is exc
    assert kwargs["path"] == "/boom"
    assert kwargs["params"] == {"foo": "bar"}
    assert response.status_code == 500


@pytest.mark.anyio
async def test_generic_exception_handler_redacts_oauth_query_params(mocker):
    capture_exception = mocker.patch("tracecat.api.common.capture_exception")
    logger = mocker.patch("tracecat.api.common.logger")
    request = _make_request(b"code=secret-code&state=secret-state&foo=bar")

    await generic_exception_handler(request, RuntimeError("boom"))

    query_params = {
        "code": REDACTED_VALUE,
        "state": REDACTED_VALUE,
        "foo": "bar",
    }
    assert (
        capture_exception.call_args.kwargs["contexts"]["tracecat.request"][
            "query_params"
        ]
        == query_params
    )
    assert logger.exception.call_args.kwargs["params"] == query_params


@pytest.mark.anyio
async def test_generic_exception_handler_redacts_webhook_secret_path(mocker):
    capture_exception = mocker.patch("tracecat.api.common.capture_exception")
    logger = mocker.patch("tracecat.api.common.logger")
    request = _make_request(
        b"",
        path="/webhooks/wf-test-webhook/super-secret/draft",
    )

    await generic_exception_handler(request, RuntimeError("boom"))

    expected_path = f"/webhooks/wf-test-webhook/{REDACTED_VALUE}/draft"
    assert capture_exception.call_args.kwargs["tags"]["http.route"] == expected_path
    assert (
        capture_exception.call_args.kwargs["contexts"]["tracecat.request"]["path"]
        == expected_path
    )
    assert logger.exception.call_args.kwargs["path"] == expected_path


def test_tracecat_exception_handler_redacts_oauth_query_params(mocker):
    capture_exception = mocker.patch("tracecat.api.common.capture_exception")
    logger = mocker.patch("tracecat.api.common.logger")
    request = _make_request(b"CODE=secret-code&STATE=secret-state&foo=bar")

    response = tracecat_exception_handler(request, TracecatException("boom"))

    query_params = {
        "CODE": REDACTED_VALUE,
        "STATE": REDACTED_VALUE,
        "foo": "bar",
    }
    assert (
        capture_exception.call_args.kwargs["contexts"]["tracecat.request"][
            "query_params"
        ]
        == query_params
    )
    assert logger.error.call_args.kwargs["params"] == query_params
    assert response.status_code == 500


def test_tracecat_exception_handler_redacts_webhook_secret_path(mocker):
    capture_exception = mocker.patch("tracecat.api.common.capture_exception")
    logger = mocker.patch("tracecat.api.common.logger")
    request = _make_request(b"", path="/webhooks/wf-test-webhook/super-secret")

    response = tracecat_exception_handler(request, TracecatException("boom"))

    expected_path = f"/webhooks/wf-test-webhook/{REDACTED_VALUE}"
    assert capture_exception.call_args.kwargs["tags"]["http.route"] == expected_path
    assert (
        capture_exception.call_args.kwargs["contexts"]["tracecat.request"]["path"]
        == expected_path
    )
    assert logger.error.call_args.kwargs["path"] == expected_path
    assert response.status_code == 500
