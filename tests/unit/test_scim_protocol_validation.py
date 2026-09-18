"""Isolated regressions for SCIM parsing and effective membership eligibility."""

import sqlite3
from uuid import UUID

import pytest
from sqlalchemy import select
from sqlalchemy.dialects import sqlite
from tracecat_ee.scim.protocol import _member_path, _member_refs, _resource_location

from tracecat import config
from tracecat.db.models import Membership, effective_group_members
from tracecat.exceptions import TracecatValidationError


@pytest.mark.parametrize(
    "path",
    [
        'members[value eq "00000000-0000-0000-0000-000000000001"]',
        'MEMBERS[VALUE EQ "00000000-0000-0000-0000-000000000001"]',
    ],
)
def test_filtered_remove_selects_only_named_user(path: str) -> None:
    selected = _member_path(path)
    assert selected is not None
    assert selected == {UUID(int=1)}
    assert {UUID(int=1), UUID(int=2), UUID(int=3)} - selected == {
        UUID(int=2),
        UUID(int=3),
    }


def test_filtered_remove_multiple_values() -> None:
    assert _member_path(
        'members[value eq "00000000-0000-0000-0000-000000000001" or value eq "00000000-0000-0000-0000-000000000002"]'
    ) == {UUID(int=1), UUID(int=2)}


@pytest.mark.parametrize(
    "path",
    [
        "members.value",
        "members[invalid]",
        'members[value ne "x"]',
        "memberships",
        "unknown",
    ],
)
def test_invalid_member_path_never_becomes_clear_all(path: str) -> None:
    with pytest.raises(TracecatValidationError):
        _member_path(path)


@pytest.mark.parametrize(
    "value", [None, "bad", {}, [{"value": "ok"}, {}], [{"value": 123}]]
)
def test_invalid_member_value_never_becomes_empty_replacement(value: object) -> None:
    with pytest.raises(TracecatValidationError):
        _member_refs(value)


def test_empty_replacement_is_explicit() -> None:
    assert _member_refs([]) == []
    assert _member_path("members") is None


def test_locations_use_configured_public_prefix(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(
        config, "TRACECAT__PUBLIC_API_URL", "https://example.com/nested/api/"
    )
    assert (
        _resource_location("Users", UUID(int=1))
        == "https://example.com/nested/api/scim/v2/Users/00000000-0000-0000-0000-000000000001"
    )


def test_derived_membership_requires_admission_and_deduplicates_sources() -> None:
    """Execute the production selectables against a mixed eligible cohort."""
    with sqlite3.connect(":memory:") as db:
        db.executescript("""
            CREATE TABLE user_role_assignment(user_id TEXT, organization_id TEXT, workspace_id TEXT);
            CREATE TABLE group_role_assignment(group_id TEXT, organization_id TEXT, workspace_id TEXT);
            CREATE TABLE group_member(group_id TEXT, user_id TEXT, added_at TEXT);
            CREATE TABLE external_group_mapping(group_id TEXT, external_group_id TEXT);
            CREATE TABLE external_group_member(external_group_id TEXT, external_user_id TEXT);
            CREATE TABLE external_user(id TEXT, user_id TEXT, organization_id TEXT, active BOOLEAN);
            CREATE TABLE organization_membership(user_id TEXT, organization_id TEXT);
            INSERT INTO group_role_assignment VALUES ('target', 'org', 'workspace');
            INSERT INTO external_group_mapping VALUES ('target','source1'),('target','source2');
            INSERT INTO external_user VALUES ('e1','eligible','org',1),('e2','pending','org',1),('e3','inactive','org',0),('e4','other-org','org',1);
            INSERT INTO external_group_member VALUES ('source1','e1'),('source2','e1'),('source1','e2'),('source1','e3'),('source1','e4');
            INSERT INTO organization_membership VALUES ('eligible','org'),('inactive','org'),('other-org','different-org');
        """)
        workspace_sql = str(
            select(Membership).compile(
                dialect=sqlite.dialect(), compile_kwargs={"literal_binds": True}
            )
        )
        assert db.execute(workspace_sql).fetchall() == [
            ("eligible", "org", "workspace")
        ]
        members_sql = str(
            select(effective_group_members).compile(
                dialect=sqlite.dialect(), compile_kwargs={"literal_binds": True}
            )
        )
        assert db.execute(members_sql).fetchall() == [("target", "eligible", None)]
        db.execute("INSERT INTO group_member VALUES ('target','manual','2026-01-01')")
        assert len(db.execute(members_sql).fetchall()) == 2
