"""Form request values must be representable as PostgreSQL JSONB."""

from typing import Any

import pytest
from pydantic import BaseModel, ValidationError

from tracecat.forms.schemas import FormConfig, FormSubmissionCreate
from tracecat.mcp.personal_access_tokens.schemas import MCPPersonalAccessTokenCreate


@pytest.mark.parametrize(
    ("model", "data", "message"),
    [
        (FormConfig, {"title": "synthetic\x00title"}, "NUL"),
        (FormConfig, {"description": "synthetic\ud800"}, "UTF-8"),
        (FormConfig, {"layout": [{"text": "synthetic\x00text"}]}, "NUL"),
        (
            FormConfig,
            {"layout": [{"field": "synthetic", "options": [{"value": "bad\x00"}]}]},
            "NUL",
        ),
        (
            FormConfig,
            {
                "layout": [
                    {
                        "field": "synthetic",
                        "visible_if": {"field": "other", "equals": float("inf")},
                    }
                ]
            },
            "finite",
        ),
        (FormSubmissionCreate, {"values": {"synthetic\x00key": "value"}}, "NUL"),
        (FormSubmissionCreate, {"values": {"synthetic": "bad\x00value"}}, "NUL"),
        (FormSubmissionCreate, {"values": {"synthetic": ["bad\x00value"]}}, "NUL"),
        (FormSubmissionCreate, {"values": {"synthetic": {"nested": "bad\x00"}}}, "NUL"),
        (FormSubmissionCreate, {"values": {"synthetic": "bad\udfff"}}, "UTF-8"),
        (FormSubmissionCreate, {"values": {"synthetic": float("nan")}}, "finite"),
        (FormSubmissionCreate, {"values": {"synthetic": [float("-inf")]}}, "finite"),
        (MCPPersonalAccessTokenCreate, {"name": "synthetic\x00name"}, "NUL"),
        (MCPPersonalAccessTokenCreate, {"name": "synthetic\ud800"}, "unicode"),
    ],
)
def test_request_rejects_values_postgres_cannot_store(
    model: type[BaseModel], data: dict[str, Any], message: str
) -> None:
    with pytest.raises(ValidationError, match=message):
        model.model_validate(data)


def test_request_preserves_valid_unicode_and_literal_escapes() -> None:
    text = "synthetic café 😀 \\u0000"
    assert FormConfig(title=text).title == text
    values = {"synthetic": {"nested": [text, -1.5, None, True]}}
    assert FormSubmissionCreate(values=values).values == values
    assert MCPPersonalAccessTokenCreate(name=text).name == text
