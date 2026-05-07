import json

from fastapi import status
from sqlalchemy.exc import DBAPIError, IntegrityError
from starlette.requests import Request
from starlette.responses import Response

from tracecat.api.common import generic_exception_handler


def _request(path: str = "/internal/cases/simple") -> Request:
    return Request(
        {
            "type": "http",
            "method": "POST",
            "path": path,
            "query_string": b"",
            "headers": [],
        }
    )


def _json_response(response: Response) -> object:
    body = response.body
    if isinstance(body, memoryview):
        body = body.tobytes()
    return json.loads(body)


def test_generic_exception_handler_maps_duplicate_case_row_link() -> None:
    exc = IntegrityError(
        "INSERT INTO case_table_row ...",
        {},
        Exception(
            'duplicate key value violates unique constraint "uq_case_table_row_link"'
        ),
    )

    response = generic_exception_handler(_request("/internal/cases/case-id/rows"), exc)

    assert response.status_code == status.HTTP_409_CONFLICT
    assert _json_response(response) == {
        "detail": {
            "code": "CASE_ROW_ALREADY_LINKED",
            "message": "This table row is already linked to the case.",
        }
    }


def test_generic_exception_handler_maps_database_type_mismatch() -> None:
    exc = DBAPIError(
        "UPDATE case_fields ...",
        {},
        Exception("invalid input for query argument $12: expected str, got list"),
    )

    response = generic_exception_handler(_request(), exc)

    assert response.status_code == status.HTTP_422_UNPROCESSABLE_CONTENT
    assert _json_response(response) == {
        "detail": {
            "code": "DATABASE_VALUE_TYPE_MISMATCH",
            "message": "A field received a value with an incompatible type.",
        }
    }
