"""Declared tier limits: apply, rerun, failures, and the CLI entry point."""

from __future__ import annotations

import contextlib
import uuid
from collections.abc import AsyncGenerator
from pathlib import Path

import orjson
import pytest
from pydantic import ValidationError
from sqlalchemy.ext.asyncio import AsyncSession

from tracecat.db.models import Tier
from tracecat.tiers import reconcile
from tracecat.tiers.enums import ConcurrencyLimitName
from tracecat.tiers.exceptions import TierLimitsDeclarationError
from tracecat.tiers.reconcile import reconcile_tier_limits
from tracecat.tiers.schemas import DeclaredTierLimits, DeclaredTierLimitsFile
from tracecat.tiers.types import TierLimitChange


def _name(label: str) -> str:
    return f"Reconcile {label} {uuid.uuid4().hex[:8]}"


def _declared(raw: dict[str, dict[str, int | None]]) -> DeclaredTierLimitsFile:
    return DeclaredTierLimitsFile.model_validate({"tiers": raw})


async def _add_tier(session: AsyncSession, name: str, **fields: int | None) -> Tier:
    tier = Tier(id=uuid.uuid4(), display_name=name, **fields)
    session.add(tier)
    await session.commit()
    return tier


async def _reload(session: AsyncSession, tier: Tier) -> Tier:
    await session.refresh(tier)
    return tier


@pytest.mark.anyio
async def test_applies_only_declared_fields(session: AsyncSession) -> None:
    tier = await _add_tier(
        session,
        _name("apply"),
        max_concurrent_actions=5,
        max_concurrent_workflows=7,
        api_rate_limit=11,
    )
    other = await _add_tier(session, _name("other"), max_concurrent_actions=3)

    changes = await reconcile_tier_limits(
        session,
        _declared(
            {
                tier.display_name: {
                    "max_concurrent_actions": 40,
                    "max_concurrent_tool_calls": 9,
                }
            }
        ),
    )

    assert changes == [
        TierLimitChange(
            tier_name=tier.display_name,
            field=ConcurrencyLimitName.MAX_CONCURRENT_ACTIONS,
            old=5,
            new=40,
        ),
        TierLimitChange(
            tier_name=tier.display_name,
            field=ConcurrencyLimitName.MAX_CONCURRENT_TOOL_CALLS,
            old=None,
            new=9,
        ),
    ]
    tier = await _reload(session, tier)
    assert tier.max_concurrent_actions == 40
    assert tier.max_concurrent_tool_calls == 9
    assert tier.max_concurrent_workflows == 7
    assert tier.max_concurrent_agent_turns is None
    assert tier.api_rate_limit == 11
    assert (await _reload(session, other)).max_concurrent_actions == 3


@pytest.mark.anyio
async def test_rerun_is_a_no_op(session: AsyncSession) -> None:
    tier = await _add_tier(session, _name("rerun"))
    declared = _declared({tier.display_name: {"max_concurrent_actions": 40}})

    assert len(await reconcile_tier_limits(session, declared)) == 1
    assert await reconcile_tier_limits(session, declared) == []
    assert (await _reload(session, tier)).max_concurrent_actions == 40


@pytest.mark.anyio
async def test_matches_display_name_ignoring_case(session: AsyncSession) -> None:
    tier = await _add_tier(session, _name("Case"))

    await reconcile_tier_limits(
        session, _declared({tier.display_name.upper(): {"max_concurrent_actions": 4}})
    )

    assert (await _reload(session, tier)).max_concurrent_actions == 4


@pytest.mark.anyio
async def test_null_sets_unlimited(session: AsyncSession) -> None:
    tier = await _add_tier(session, _name("null"), max_concurrent_actions=5)

    changes = await reconcile_tier_limits(
        session, _declared({tier.display_name: {"max_concurrent_actions": None}})
    )

    assert [(c.old, c.new) for c in changes] == [(5, None)]
    assert (await _reload(session, tier)).max_concurrent_actions is None


@pytest.mark.anyio
async def test_dry_run_writes_nothing(session: AsyncSession) -> None:
    tier = await _add_tier(session, _name("dry"), max_concurrent_actions=5)

    changes = await reconcile_tier_limits(
        session,
        _declared({tier.display_name: {"max_concurrent_actions": 40}}),
        dry_run=True,
    )

    assert [(c.old, c.new) for c in changes] == [(5, 40)]
    assert (await _reload(session, tier)).max_concurrent_actions == 5


@pytest.mark.anyio
async def test_unknown_tier_fails_without_writing(session: AsyncSession) -> None:
    tier = await _add_tier(session, _name("known"), max_concurrent_actions=5)
    missing = _name("missing")

    with pytest.raises(TierLimitsDeclarationError, match=missing):
        await reconcile_tier_limits(
            session,
            _declared(
                {
                    tier.display_name: {"max_concurrent_actions": 40},
                    missing: {"max_concurrent_actions": 40},
                }
            ),
        )

    assert (await _reload(session, tier)).max_concurrent_actions == 5


@pytest.mark.anyio
async def test_ambiguous_name_fails(session: AsyncSession) -> None:
    name = _name("dup")
    first = await _add_tier(session, name, max_concurrent_actions=5)
    await _add_tier(session, name.lower(), max_concurrent_actions=5)

    with pytest.raises(TierLimitsDeclarationError, match="More than one tier"):
        await reconcile_tier_limits(
            session, _declared({name: {"max_concurrent_actions": 40}})
        )

    assert (await _reload(session, first)).max_concurrent_actions == 5


@pytest.mark.anyio
async def test_names_differing_only_by_case_fail(session: AsyncSession) -> None:
    tier = await _add_tier(session, _name("twice"))

    with pytest.raises(TierLimitsDeclarationError, match="unique ignoring case"):
        await reconcile_tier_limits(
            session,
            _declared(
                {
                    tier.display_name: {"max_concurrent_actions": 4},
                    tier.display_name.upper(): {"max_concurrent_actions": 8},
                }
            ),
        )


@pytest.mark.anyio
async def test_empty_file_is_a_no_op(session: AsyncSession) -> None:
    assert await reconcile_tier_limits(session, DeclaredTierLimitsFile()) == []


def test_declared_fields_are_the_concurrency_limits() -> None:
    assert set(DeclaredTierLimits.model_fields) == set(ConcurrencyLimitName)


@pytest.mark.parametrize(
    "raw",
    [
        {"tiers": {"Default": {"api_rate_limit": 10}}},
        {"tiers": {"Default": {"max_concurrent_actions": 0}}},
        {"tiers": {"Default": {"max_concurrent_actions": "40"}}},
        {"tier": {}},
    ],
)
def test_rejects_invalid_declarations(raw: dict[str, object]) -> None:
    with pytest.raises(ValidationError):
        DeclaredTierLimitsFile.model_validate_json(orjson.dumps(raw))


def test_main_rejects_invalid_file(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    path = tmp_path / "limits.json"
    path.write_text('{"tiers": {"Default": {"api_rate_limit": 10}}}')

    assert reconcile.main(["--file", str(path)]) == 1
    assert "api_rate_limit" in capsys.readouterr().err


def test_main_rejects_missing_file(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    assert reconcile.main(["--file", str(tmp_path / "absent.json")]) == 1
    assert "cannot read" in capsys.readouterr().err


@pytest.mark.anyio
async def test_run_prints_field_diff(
    session: AsyncSession,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    tier = await _add_tier(session, _name("print"), max_concurrent_actions=5)

    @contextlib.asynccontextmanager
    async def _session() -> AsyncGenerator[AsyncSession]:
        yield session

    monkeypatch.setattr(
        reconcile, "get_async_session_bypass_rls_context_manager", _session
    )
    declared = _declared({tier.display_name: {"max_concurrent_actions": 40}})

    assert await reconcile._run(declared, dry_run=True) == 0
    assert capsys.readouterr().out.splitlines() == [
        f"{tier.display_name}.max_concurrent_actions: 5 -> 40",
        "dry run: 1 change(s), nothing written",
    ]

    assert await reconcile._run(declared, dry_run=False) == 0
    assert capsys.readouterr().out.splitlines() == [
        f"{tier.display_name}.max_concurrent_actions: 5 -> 40",
        "applied 1 change(s)",
    ]

    assert await reconcile._run(declared, dry_run=False) == 0
    assert capsys.readouterr().out.splitlines() == ["no changes"]
