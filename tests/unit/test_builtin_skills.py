"""Tests for always-on built-in Workspace Chat skills.

Covers the reserved skill-name namespace, the ``builtin_skills`` config field
threading across the Temporal payload boundary, and the executor staging that
copies vendored skill directories into the per-run skills directory.
"""

from __future__ import annotations

import re
from pathlib import Path
from types import SimpleNamespace
from typing import Any, cast

import pytest
import yaml

from tracecat import config
from tracecat.agent.executor.activity import SandboxedAgentExecutor
from tracecat.agent.skill.schemas import (
    RESERVED_SKILL_NAME_PREFIX,
    SkillCreate,
)

VENDORED_SKILLS_ROOT = Path(config.TRACECAT__COPILOT_SKILLS_DIR)
VENDORED_SKILLS_SKIP_REASON = (
    f"No vendored workspace-chat skills at {config.TRACECAT__COPILOT_SKILLS_DIR}. "
    "The `plugin-skills` Dockerfile stage copies them in at image build time, so "
    "a source checkout does not carry them. Set TRACECAT__COPILOT_SKILLS_DIR to "
    "a populated directory to run these checks locally."
)


def require_vendored_skills() -> Path:
    """Return the vendored tree or skip checks that require its content."""
    from tracecat.agent.skill.builtin import BUILTIN_WORKSPACE_CHAT_SKILLS

    if any(
        not (VENDORED_SKILLS_ROOT / name).is_dir()
        for name in BUILTIN_WORKSPACE_CHAT_SKILLS
    ):
        pytest.skip(VENDORED_SKILLS_SKIP_REASON)
    return VENDORED_SKILLS_ROOT


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

    def test_reserved_prefix_matches_builtin_constant(self):
        # Keep the core validator prefix in sync with the built-in package constant.
        from tracecat.agent.skill.builtin import BUILTIN_SKILL_NAME_PREFIX

        assert RESERVED_SKILL_NAME_PREFIX == BUILTIN_SKILL_NAME_PREFIX


class TestBuiltinSkillsConstant:
    """The built-in skill catalog is well-formed and present on disk."""

    def test_all_builtin_skills_use_reserved_prefix(self):
        from tracecat.agent.skill.builtin import (
            BUILTIN_SKILL_NAME_PREFIX,
            BUILTIN_WORKSPACE_CHAT_SKILLS,
        )

        assert BUILTIN_WORKSPACE_CHAT_SKILLS == (
            "tracecat-workspace-chat",
            "tracecat-automation-best-practices",
            "tracecat-slackbot-best-practices",
        )
        for name in BUILTIN_WORKSPACE_CHAT_SKILLS:
            assert name.startswith(BUILTIN_SKILL_NAME_PREFIX)

    def test_each_builtin_skill_has_skill_md(self):
        from tracecat.agent.skill.builtin import BUILTIN_WORKSPACE_CHAT_SKILLS

        root = require_vendored_skills()
        for name in BUILTIN_WORKSPACE_CHAT_SKILLS:
            assert (root / name / "SKILL.md").is_file()


class TestVendoredSkillContent:
    """Content checks against the skills vendored from tracecat-plugins."""

    def test_frontmatter_name_matches_directory(self):
        from tracecat.agent.skill.builtin import BUILTIN_WORKSPACE_CHAT_SKILLS

        root = require_vendored_skills()
        for name in BUILTIN_WORKSPACE_CHAT_SKILLS:
            skill_md = root / name / "SKILL.md"
            match = re.match(
                r"\A---\r?\n(?P<frontmatter>.*?)\r?\n---(?:\r?\n|\Z)",
                skill_md.read_text(encoding="utf-8"),
                re.DOTALL,
            )
            assert match is not None, f"Missing YAML frontmatter in {skill_md}"
            frontmatter = yaml.safe_load(match.group("frontmatter"))
            assert isinstance(frontmatter, dict)
            assert frontmatter.get("name") == name

    def test_relative_reference_links_resolve(self):
        from tracecat.agent.skill.builtin import BUILTIN_WORKSPACE_CHAT_SKILLS

        root = require_vendored_skills()
        for name in BUILTIN_WORKSPACE_CHAT_SKILLS:
            skill_root = root / name
            for markdown in skill_root.rglob("*.md"):
                content = markdown.read_text(encoding="utf-8")
                for target in re.findall(r"\]\((references/[^)#?]+\.md)\)", content):
                    assert (markdown.parent / target).is_file(), (
                        f"Broken reference link {target!r} in {markdown}"
                    )

    def test_no_todo_placeholders(self):
        from tracecat.agent.skill.builtin import BUILTIN_WORKSPACE_CHAT_SKILLS

        root = require_vendored_skills()
        for name in BUILTIN_WORKSPACE_CHAT_SKILLS:
            for markdown in (root / name).rglob("*.md"):
                assert "TODO:" not in markdown.read_text(encoding="utf-8"), (
                    f"TODO placeholder found in {markdown}"
                )

    def test_workspace_chat_adapter_contains_local_docs(self):
        root = require_vendored_skills()
        docs_root = root / "tracecat-workspace-chat" / "references" / "docs"

        assert (docs_root / "docs.json").is_file()
        assert (docs_root / "agents" / "workspace-chat.mdx").is_file()
        assert (docs_root / "automations" / "workflows.mdx").is_file()
        assert (docs_root / "snippets" / "mcp-tools.mdx").is_file()
        assert (docs_root / "automations" / "core-actions" / "_manifest.yaml").is_file()
        assert not any(
            path.suffix.lower() in {".gif", ".jpeg", ".jpg", ".png", ".svg", ".webp"}
            for path in docs_root.rglob("*")
            if path.is_file()
        )


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
            builtin_skills=["tracecat-automation-best-practices"],
        )
        restored = agent_config_from_payload(agent_config_to_payload(config))
        assert restored.builtin_skills == ["tracecat-automation-best-practices"]

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

        from tracecat.agent.skill.builtin import BUILTIN_WORKSPACE_CHAT_SKILLS

        svc = SimpleNamespace(session=object(), role=object())
        resolve = AgentSessionService._resolve_builtin_workspace_chat_skills.__get__(
            svc
        )
        result = await resolve()
        assert result == list(BUILTIN_WORKSPACE_CHAT_SKILLS)
        assert result[0] == "tracecat-workspace-chat"
        assert "tracecat-automation-best-practices" in result

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
    """The executor copies vendored built-in skills into the run skills dir."""

    @pytest.mark.anyio
    async def test_noop_when_no_builtin_skills(self, tmp_path: Path):
        skills_dir = tmp_path / "skills"
        skills_dir.mkdir()
        await _executor_with_builtin_skills(None).stage(skills_dir)
        assert list(skills_dir.iterdir()) == []

    @pytest.mark.anyio
    async def test_stages_from_vendored_directory(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ):
        """The vendored directory is the source of truth when it exists."""
        from tracecat.agent.executor import activity as activity_mod

        vendored_root = tmp_path / "vendored"
        vendored_skill = vendored_root / "tracecat-automation-best-practices"
        vendored_skill.mkdir(parents=True)
        (vendored_skill / "SKILL.md").write_text("vendored content")
        monkeypatch.setattr(
            activity_mod.app_config,
            "TRACECAT__COPILOT_SKILLS_DIR",
            str(vendored_root),
        )

        skills_dir = tmp_path / "skills"
        skills_dir.mkdir()
        await _executor_with_builtin_skills(
            ["tracecat-automation-best-practices"]
        ).stage(skills_dir)
        assert (
            skills_dir / "tracecat-automation-best-practices" / "SKILL.md"
        ).read_text() == "vendored content"

    @pytest.mark.anyio
    async def test_falls_back_to_package_when_vendored_dir_absent(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ):
        """An image without the vendored directory still stages packaged skills."""
        from tracecat.agent.executor import activity as activity_mod

        monkeypatch.setattr(
            activity_mod.app_config,
            "TRACECAT__COPILOT_SKILLS_DIR",
            str(tmp_path / "does-not-exist"),
        )

        package_root = tmp_path / "package"
        packaged_skill = package_root / "tracecat-automation-best-practices"
        packaged_skill.mkdir(parents=True)
        (packaged_skill / "SKILL.md").write_text("packaged content")

        def fake_files(package: str) -> Path:
            assert package == "tracecat.agent.skill.builtin"
            return package_root

        monkeypatch.setattr(activity_mod, "files", fake_files)

        skills_dir = tmp_path / "skills"
        skills_dir.mkdir()
        await _executor_with_builtin_skills(
            ["tracecat-automation-best-practices"]
        ).stage(skills_dir)
        assert (
            skills_dir / "tracecat-automation-best-practices" / "SKILL.md"
        ).read_text() == "packaged content"

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
        staged = skills_dir / "tracecat-automation-best-practices"
        staged.mkdir(parents=True)
        (staged / "SKILL.md").write_text("builtin content")

        @asynccontextmanager
        async def _fake_with_session(*, role=None):  # noqa: ANN001
            yield object()

        monkeypatch.setattr(
            activity_mod.SkillService, "with_session", _fake_with_session
        )

        resolved = SimpleNamespace(
            skill_name="tracecat-automation-best-practices",
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


class TestWorkspaceChatPrompt:
    """Workspace Chat enters through the host adapter before generic guidance."""

    def test_requires_adapter_for_all_platform_work(self):
        from tracecat.workspaces.prompts import WorkspaceCopilotPrompts

        instructions = WorkspaceCopilotPrompts().instructions
        platform_section = instructions.split("<platform-guidance>", maxsplit=1)[1]
        platform_section = platform_section.split("</platform-guidance>", maxsplit=1)[0]
        normalized_section = " ".join(platform_section.split())

        assert "tracecat-workspace-chat" in normalized_section
        assert "any Tracecat platform tool" in normalized_section

    def test_workflow_skill_order_is_adapter_then_generic(self):
        from tracecat.workspaces.prompts import WorkspaceCopilotPrompts

        instructions = WorkspaceCopilotPrompts().instructions
        workflow_section = instructions.split("<workflows>", maxsplit=1)[1]
        workflow_section = workflow_section.split("</workflows>", maxsplit=1)[0]

        assert workflow_section.index("tracecat-workspace-chat") < (
            workflow_section.index("tracecat-automation-best-practices")
        )


class TestWorkflowActionDescriptions:
    """Every Workspace Chat workflow tool points to both guidance layers."""

    def test_all_core_workflow_actions_reference_both_skills(self):
        from collections.abc import Mapping

        from tracecat_registry.core import workflow

        action_descriptions: dict[str, str] = {}
        for candidate in vars(workflow).values():
            key = getattr(candidate, "__tracecat_udf_key", None)
            if not isinstance(key, str) or not key.startswith("core.workflow."):
                continue
            metadata = cast(
                Mapping[str, object],
                getattr(candidate, "__tracecat_udf_kwargs", {}),
            )
            description = metadata.get("description")
            assert isinstance(description, str)
            action_descriptions[key] = description

        assert action_descriptions
        for key, description in action_descriptions.items():
            assert "`tracecat-workspace-chat`" in description, key
            assert "`tracecat-automation-best-practices`" in description, key
