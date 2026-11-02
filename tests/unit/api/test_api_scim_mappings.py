"""HTTP-level tests for the SCIM group mapping administration routes."""

from __future__ import annotations

import uuid
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

import pytest
from fastapi import status
from fastapi.testclient import TestClient
from tracecat_ee.scim.schemas import ExternalGroupMappingRead, ExternalGroupRead

from tracecat import config
from tracecat.auth.types import Role
from tracecat.exceptions import (
    EntitlementRequired,
    ScopeDeniedError,
    TracecatNotFoundError,
)
from tracecat.pagination import Page, PageParams
from tracecat.tiers.enums import Entitlement

EXTERNAL_GROUPS = "/scim/external-groups"
MAPPINGS = "/scim/mappings"
# Fixed so parametrized ids match across xdist workers, which collect separately.
MAPPING_ID = uuid.UUID("00000000-0000-4000-8000-00000000beef")


def _mapping() -> ExternalGroupMappingRead:
    return ExternalGroupMappingRead(
        id=uuid.uuid4(),
        external_group_id=uuid.uuid4(),
        external_group_external_id="idp-eng",
        external_group_display_name="Engineering",
        group_id=uuid.uuid4(),
        group_name="engineers",
    )


@pytest.mark.anyio
async def test_list_external_groups_returns_member_counts(
    client: TestClient, test_admin_role: Role
) -> None:
    """The listing carries the count an admin needs to pick a source group."""
    groups = [
        ExternalGroupRead(
            id=uuid.uuid4(),
            external_id="idp-eng",
            display_name="Engineering",
            member_count=3,
        )
    ]

    with (
        patch(
            "tracecat_ee.scim.router.check_entitlement",
            new=AsyncMock(return_value=None),
        ),
        patch(
            "tracecat_ee.scim.service.SCIMService.list_external_groups",
            new=AsyncMock(return_value=Page(items=groups)),
        ),
    ):
        response = client.get(EXTERNAL_GROUPS)

    assert response.status_code == status.HTTP_200_OK
    body = response.json()["items"]
    assert len(body) == 1
    assert body[0]["external_id"] == "idp-eng"
    assert body[0]["member_count"] == 3


@pytest.mark.anyio
async def test_list_mappings_returns_joined_detail(
    client: TestClient, test_admin_role: Role
) -> None:
    """Both sides are present, so the UI renders without a second request."""
    mapping = _mapping()

    with (
        patch(
            "tracecat_ee.scim.router.check_entitlement",
            new=AsyncMock(return_value=None),
        ),
        patch(
            "tracecat_ee.scim.service.SCIMService.list_mappings",
            new=AsyncMock(return_value=Page(items=[mapping], next_cursor="next-page")),
        ) as list_mappings,
    ):
        response = client.get(MAPPINGS, params={"limit": 2, "cursor": "current-page"})

    assert response.status_code == status.HTTP_200_OK
    list_mappings.assert_awaited_once_with(
        page=PageParams(limit=2, cursor="current-page")
    )
    assert response.json()["next_cursor"] == "next-page"
    row = response.json()["items"][0]
    assert row["external_group_display_name"] == "Engineering"
    assert row["external_group_external_id"] == "idp-eng"
    assert row["group_name"] == "engineers"


@pytest.mark.anyio
@pytest.mark.parametrize("limit", [0, config.TRACECAT__LIMIT_CURSOR_MAX + 1])
async def test_mapping_page_limit_is_validated(
    client: TestClient, test_admin_role: Role, limit: int
) -> None:
    with (
        patch(
            "tracecat_ee.scim.router.check_entitlement",
            new=AsyncMock(return_value=None),
        ),
        patch(
            "tracecat_ee.scim.service.SCIMService.list_mappings", new=AsyncMock()
        ) as list_mappings,
    ):
        response = client.get(MAPPINGS, params={"limit": limit})
    assert response.status_code == 422
    list_mappings.assert_not_awaited()


@pytest.mark.anyio
async def test_create_returns_the_joined_mapping(
    client: TestClient, test_admin_role: Role
) -> None:
    """Creating returns the mapping with both sides already resolved."""
    mapping = _mapping()
    created = SimpleNamespace(id=mapping.id)

    with (
        patch(
            "tracecat_ee.scim.router.check_entitlement",
            new=AsyncMock(return_value=None),
        ),
        patch(
            "tracecat_ee.scim.service.SCIMService.create_mapping",
            new=AsyncMock(return_value=created),
        ),
        patch(
            "tracecat_ee.scim.service.SCIMService.get_mapping",
            new=AsyncMock(return_value=mapping),
        ),
    ):
        response = client.post(
            MAPPINGS,
            json={
                "external_group_id": str(mapping.external_group_id),
                "group_id": str(mapping.group_id),
            },
        )

    assert response.status_code == status.HTTP_200_OK
    assert response.json()["id"] == str(mapping.id)


@pytest.mark.anyio
async def test_create_against_a_missing_group_is_404(
    client: TestClient, test_admin_role: Role
) -> None:
    """A group outside this organization is not found, never created."""
    with (
        patch(
            "tracecat_ee.scim.router.check_entitlement",
            new=AsyncMock(return_value=None),
        ),
        patch(
            "tracecat_ee.scim.service.SCIMService.create_mapping",
            new=AsyncMock(side_effect=TracecatNotFoundError("Group not found")),
        ),
    ):
        response = client.post(
            MAPPINGS,
            json={
                "external_group_id": str(uuid.uuid4()),
                "group_id": str(uuid.uuid4()),
            },
        )

    assert response.status_code == status.HTTP_404_NOT_FOUND


@pytest.mark.anyio
async def test_delete_returns_no_content(
    client: TestClient, test_admin_role: Role
) -> None:
    """Removing a mapping answers 204."""
    with (
        patch(
            "tracecat_ee.scim.router.check_entitlement",
            new=AsyncMock(return_value=None),
        ),
        patch(
            "tracecat_ee.scim.service.SCIMService.delete_mapping",
            new=AsyncMock(return_value=None),
        ),
    ):
        response = client.request("DELETE", f"{MAPPINGS}/{uuid.uuid4()}")

    assert response.status_code == status.HTTP_204_NO_CONTENT


@pytest.mark.anyio
async def test_delete_of_a_missing_mapping_is_404(
    client: TestClient, test_admin_role: Role
) -> None:
    """A mapping id from another organization is not found."""
    with (
        patch(
            "tracecat_ee.scim.router.check_entitlement",
            new=AsyncMock(return_value=None),
        ),
        patch(
            "tracecat_ee.scim.service.SCIMService.delete_mapping",
            new=AsyncMock(side_effect=TracecatNotFoundError("missing")),
        ),
    ):
        response = client.request("DELETE", f"{MAPPINGS}/{uuid.uuid4()}")

    assert response.status_code == status.HTTP_404_NOT_FOUND


@pytest.mark.anyio
@pytest.mark.parametrize(
    ("method", "path", "target"),
    [
        ("GET", EXTERNAL_GROUPS, "list_external_groups"),
        ("GET", MAPPINGS, "list_mappings"),
        ("POST", MAPPINGS, "create_mapping"),
        ("DELETE", f"{MAPPINGS}/{MAPPING_ID}", "delete_mapping"),
    ],
)
async def test_a_role_without_the_scope_is_refused(
    client: TestClient, test_admin_role: Role, method: str, path: str, target: str
) -> None:
    """Every route refuses a caller the service's scope check rejects."""
    denied = ScopeDeniedError(
        required_scopes=["org:rbac:read"], missing_scopes=["org:rbac:read"]
    )

    with (
        patch(
            "tracecat_ee.scim.router.check_entitlement",
            new=AsyncMock(return_value=None),
        ),
        patch(
            f"tracecat_ee.scim.service.SCIMService.{target}",
            new=AsyncMock(side_effect=denied),
        ),
    ):
        response = client.request(
            method,
            path,
            json={"external_group_id": str(uuid.uuid4()), "group_id": str(uuid.uuid4())}
            if method == "POST"
            else None,
        )

    assert response.status_code == status.HTTP_403_FORBIDDEN


@pytest.mark.anyio
@pytest.mark.parametrize(
    ("method", "path"),
    [
        ("GET", EXTERNAL_GROUPS),
        ("GET", MAPPINGS),
        ("POST", MAPPINGS),
        ("DELETE", f"{MAPPINGS}/{MAPPING_ID}"),
    ],
)
async def test_an_organization_without_the_entitlement_is_refused(
    client: TestClient, test_admin_role: Role, method: str, path: str
) -> None:
    """The router-level gate refuses every route without RBAC_ADDONS."""
    with patch(
        "tracecat_ee.scim.router.check_entitlement",
        new=AsyncMock(side_effect=EntitlementRequired(Entitlement.RBAC_ADDONS)),
    ):
        response = client.request(
            method,
            path,
            json={"external_group_id": str(uuid.uuid4()), "group_id": str(uuid.uuid4())}
            if method == "POST"
            else None,
        )

    assert response.status_code in {
        status.HTTP_402_PAYMENT_REQUIRED,
        status.HTTP_403_FORBIDDEN,
    }
