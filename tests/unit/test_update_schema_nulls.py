"""Update schemas reject explicit nulls for NOT NULL columns."""

import pytest
from pydantic import BaseModel, ValidationError
from tracecat_ee.admin.organizations.schemas import OrgUpdate

from tracecat.cases.schemas import CaseUpdate
from tracecat.webhooks.schemas import WebhookUpdate


@pytest.mark.parametrize(
    ("schema", "field"),
    [
        (CaseUpdate, "summary"),
        (CaseUpdate, "description"),
        (WebhookUpdate, "status"),
        (WebhookUpdate, "include_headers"),
        (OrgUpdate, "name"),
        (OrgUpdate, "slug"),
        (OrgUpdate, "is_active"),
    ],
)
def test_update_rejects_explicit_null(schema: type[BaseModel], field: str) -> None:
    with pytest.raises(ValidationError, match="must not be null"):
        schema.model_validate({field: None})
    assert field not in schema.model_validate({}).model_fields_set
