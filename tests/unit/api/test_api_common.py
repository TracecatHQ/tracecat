import pytest
from starlette.requests import Request

from tracecat.api.common import generic_exception_handler


@pytest.mark.anyio
async def test_generic_exception_handler_logs_with_exception(mocker):
    logger = mocker.patch("tracecat.api.common.logger")
    exc = RuntimeError("boom")
    request = Request(
        {
            "type": "http",
            "method": "GET",
            "path": "/boom",
            "query_string": b"foo=bar",
            "headers": [],
            "server": ("testserver", 80),
            "scheme": "http",
            "client": ("testclient", 50000),
        }
    )

    response = await generic_exception_handler(request, exc)

    logger.exception.assert_called_once()
    logger.error.assert_not_called()
    args, kwargs = logger.exception.call_args
    assert args == ("Unexpected error",)
    assert kwargs["exc"] is exc
    assert kwargs["path"] == "/boom"
    assert str(kwargs["params"]) == "foo=bar"
    assert response.status_code == 500
