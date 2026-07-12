"""Tests for always-on built-in (EE) workspace-chat skills.

Covers the reserved skill-name namespace, the ``builtin_skills`` config field
threading across the Temporal payload boundary, and the executor staging that
copies packaged skill directories into the per-run skills directory.
"""

from __future__ import annotations

from pathlib import Path
from types import SimpleNamespace
from typing import Any

import pytest

from tracecat.agent.executor.activity import SandboxedAgentExecutor
from tracecat.agent.skill.schemas import (
    RESERVED_SKILL_NAME_PREFIX,
    SkillCreate,
)


class TestReservedSkillNamespace:
    """User/preset skill names may not use the reserved platform prefix."""

    def test_reserved_prefix_rejected(self):
        with pytest.raises(ValueError, match="reserved prefix"):
            SkillCreate(name=f"{RESERVED_SKILL_NAME_PREFIX}manage-workflows")

    def test_non_reserved_name_allowed(self):
        skill = SkillCreate(name="my-custom-skill")
        assert skill.name == "my-custom-skill"

    def test_lookup_name_accepts_reserved_prefix(self):
        # Legacy skills named before the prefix was reserved must remain
        # addressable by name — only create/publish paths reject the prefix.
        from pydantic import TypeAdapter

        from tracecat.agent.skill.schemas import SkillName

        adapter = TypeAdapter(SkillName)
        assert (
            adapter.validate_python(f"{RESERVED_SKILL_NAME_PREFIX}legacy")
            == f"{RESERVED_SKILL_NAME_PREFIX}legacy"
        )

    def test_reserved_prefix_matches_ee_constant(self):
        # Keep the core validator prefix in sync with the EE package constant.
        from tracecat_ee.workspace_chat.skills import BUILTIN_SKILL_NAME_PREFIX

        assert RESERVED_SKILL_NAME_PREFIX == BUILTIN_SKILL_NAME_PREFIX


class TestBuiltinSkillsConstant:
    """The EE built-in skill catalog is well-formed and present on disk."""

    def test_all_builtin_skills_use_reserved_prefix(self):
        from tracecat_ee.workspace_chat.skills import (
            BUILTIN_SKILL_NAME_PREFIX,
            BUILTIN_WORKSPACE_CHAT_SKILLS,
        )

        assert BUILTIN_WORKSPACE_CHAT_SKILLS
        for name in BUILTIN_WORKSPACE_CHAT_SKILLS:
            assert name.startswith(BUILTIN_SKILL_NAME_PREFIX)

    def test_each_builtin_skill_has_skill_md(self):
        from importlib.resources import files

        from tracecat_ee.workspace_chat.skills import BUILTIN_WORKSPACE_CHAT_SKILLS

        root = files("tracecat_ee.workspace_chat.skills")
        for name in BUILTIN_WORKSPACE_CHAT_SKILLS:
            assert (root / name / "SKILL.md").is_file()


class TestBuiltinSkillsPayloadThreading:
    """``builtin_skills`` survives the AgentConfig <-> payload round-trip."""

    def test_round_trip_preserves_builtin_skills(self):
        from tracecat.agent.types import AgentConfig
        from tracecat.agent.workflow_config import (
            agent_config_from_payload,
            agent_config_to_payload,
        )

        config = AgentConfig(
            model_name="claude",
            model_provider="anthropic",
            builtin_skills=["tracecat-manage-workflows"],
        )
        restored = agent_config_from_payload(agent_config_to_payload(config))
        assert restored.builtin_skills == ["tracecat-manage-workflows"]

    def test_round_trip_defaults_to_none(self):
        from tracecat.agent.types import AgentConfig
        from tracecat.agent.workflow_config import (
            agent_config_from_payload,
            agent_config_to_payload,
        )

        config = AgentConfig(model_name="claude", model_provider="anthropic")
        restored = agent_config_from_payload(agent_config_to_payload(config))
        assert restored.builtin_skills is None


def _executor_with_builtin_skills(names: list[str] | None) -> Any:
    """Build a minimal stand-in exposing only what _stage_builtin_skills needs."""
    fake = SimpleNamespace(
        input=SimpleNamespace(config=SimpleNamespace(builtin_skills=names))
    )
    # Bind the unbound coroutine method to the fake instance.
    fake.stage = SandboxedAgentExecutor._stage_builtin_skills.__get__(fake)
    return fake


class TestResolveBuiltinWorkspaceChatSkills:
    """The config-build gate returns built-in skills only when entitled."""

    @pytest.mark.anyio
    async def test_returns_skills_when_entitled(self, monkeypatch):
        from tracecat.agent.session import service as session_service
        from tracecat.agent.session.service import AgentSessionService

        async def _entitled(session, role):  # noqa: ANN001
            return True

        # Patch the name bound in `service` (imported by value), not in `policy`.
        monkeypatch.setattr(session_service, "is_workspace_chat_entitled", _entitled)

        from tracecat_ee.workspace_chat.skills import BUILTIN_WORKSPACE_CHAT_SKILLS

        svc = SimpleNamespace(session=object(), role=object())
        resolve = AgentSessionService._resolve_builtin_workspace_chat_skills.__get__(
            svc
        )
        result = await resolve()
        assert result == list(BUILTIN_WORKSPACE_CHAT_SKILLS)
        assert "tracecat-manage-workflows" in result

    @pytest.mark.anyio
    async def test_returns_none_when_not_entitled(self, monkeypatch):
        from tracecat.agent.session import service as session_service
        from tracecat.agent.session.service import AgentSessionService

        async def _not_entitled(session, role):  # noqa: ANN001
            return False

        monkeypatch.setattr(
            session_service, "is_workspace_chat_entitled", _not_entitled
        )

        svc = SimpleNamespace(session=object(), role=object())
        resolve = AgentSessionService._resolve_builtin_workspace_chat_skills.__get__(
            svc
        )
        assert await resolve() is None


class TestStageBuiltinSkills:
    """The executor copies packaged built-in skills into the run skills dir."""

    @pytest.mark.anyio
    async def test_noop_when_no_builtin_skills(self, tmp_path: Path):
        skills_dir = tmp_path / "skills"
        skills_dir.mkdir()
        await _executor_with_builtin_skills(None).stage(skills_dir)
        assert list(skills_dir.iterdir()) == []

    @pytest.mark.anyio
    async def test_stages_real_builtin_skill(self, tmp_path: Path):
        skills_dir = tmp_path / "skills"
        skills_dir.mkdir()
        await _executor_with_builtin_skills(["tracecat-manage-workflows"]).stage(
            skills_dir
        )
        assert (skills_dir / "tracecat-manage-workflows" / "SKILL.md").is_file()

    @pytest.mark.anyio
    async def test_skips_unknown_or_unprefixed_names(self, tmp_path: Path):
        skills_dir = tmp_path / "skills"
        skills_dir.mkdir()
        await _executor_with_builtin_skills(
            ["not-prefixed", "tracecat-does-not-exist"]
        ).stage(skills_dir)
        assert list(skills_dir.iterdir()) == []


class TestStageResolvedSkillsCollision:
    """Resolved skills staged after built-ins skip name collisions."""

    @pytest.mark.anyio
    async def test_skips_resolved_skill_colliding_with_builtin(
        self, tmp_path: Path, monkeypatch
    ):
        import uuid
        from contextlib import asynccontextmanager

        from tracecat.agent.executor import activity as activity_mod

        skills_dir = tmp_path / "skills"
        staged = skills_dir / "tracecat-manage-workflows"
        staged.mkdir(parents=True)
        (staged / "SKILL.md").write_text("builtin content")

        @asynccontextmanager
        async def _fake_with_session(*, role=None):  # noqa: ANN001
            yield object()

        monkeypatch.setattr(
            activity_mod.SkillService, "with_session", _fake_with_session
        )

        resolved = SimpleNamespace(
            skill_slug="tracecat-manage-workflows",
            manifest_sha256="0" * 64,
            skill_version_id=uuid.uuid4(),
        )

        async def _fail_materialize(**kwargs: Any):
            raise AssertionError("colliding skill must not be materialized")

        fake = SimpleNamespace(
            input=SimpleNamespace(
                config=SimpleNamespace(resolved_skills=[resolved]),
                role=object(),
            ),
            _ensure_cached_skill_dir=_fail_materialize,
        )
        stage = SandboxedAgentExecutor._stage_resolved_skills.__get__(fake)
        await stage(skills_dir)

        # The staged built-in is untouched and nothing new was copied.
        assert (staged / "SKILL.md").read_text() == "builtin content"
        assert list(skills_dir.iterdir()) == [staged]
