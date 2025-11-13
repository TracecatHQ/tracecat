from io import BytesIO

import pytest
from fastapi import HTTPException, status
from starlette.datastructures import Headers, UploadFile

from tracecat.tables.router import _read_csv_upload_with_limit


@pytest.mark.anyio
async def test_read_csv_upload_with_limit_allows_within_limit():
    content = b"column_a,column_b\nvalue1,value2\n"
    upload = UploadFile(
        filename="test.csv",
        file=BytesIO(content),
        headers=Headers({"content-type": "text/csv"}),
    )

    result = await _read_csv_upload_with_limit(upload, max_size=len(content) + 10)

    assert result == content


@pytest.mark.anyio
async def test_read_csv_upload_with_limit_rejects_oversized_file():
    limit = 10
    content = b"x" * (limit + 1)
    upload = UploadFile(
        filename="large.csv",
        file=BytesIO(content),
        headers=Headers({"content-type": "text/csv"}),
    )

    with pytest.raises(HTTPException) as exc:
        await _read_csv_upload_with_limit(upload, max_size=limit)

    assert exc.value.status_code == status.HTTP_413_CONTENT_TOO_LARGE


@pytest.mark.anyio
async def test_read_csv_upload_with_limit_rejects_empty_file():
    upload = UploadFile(
        filename="empty.csv",
        file=BytesIO(b""),
        headers=Headers({"content-type": "text/csv"}),
    )

    with pytest.raises(HTTPException) as exc:
        await _read_csv_upload_with_limit(upload, max_size=10)

    assert exc.value.status_code == status.HTTP_400_BAD_REQUEST
