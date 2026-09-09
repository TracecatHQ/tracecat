"""Origin separation and materialization of platform and workspace skills."""

import uuid
from contextlib import asynccontextmanager
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock

import orjson
import pytest
from pydantic import ValidationError

from tracecat.agent.executor import activity as activity_module
from tracecat.agent.executor.activity import SandboxedAgentExecutor
from tracecat.agent.skill.builtin import PLATFORM_SKILLS
from tracecat.agent.skill.builtin.staging import stage_platform_skill_plugin
from tracecat.agent.skill.frontmatter import parse_skill_markdown
from tracecat.agent.skill.schemas import SkillReadMinimal
from tracecat.agent.skill.types import ResolvedSkillRef, SkillOrigin
from tracecat.agent.types import AgentConfig
from tracecat.agent.workflow_config import (
    agent_config_from_payload,
    agent_config_to_payload,
)
from tracecat.agent.workflow_schemas import ResolvedSkillRefPayload


def test_platform_plugin_preserves_portable_names_and_qualifies_references(
    tmp_path: Path,
) -> None:
    vendored = tmp_path / "vendored"
    source = vendored / "tracecat-workspace-chat"
    source.mkdir(parents=True)
    (source / "SKILL.md").write_text(
        "---\nname: tracecat-workspace-chat\ndescription: Platform guidance\n---\n"
        "Follow `$tracecat-automation-best-practices`."
    )
    references = source / "references"
    references.mkdir()
    (references / "guide.md").write_text("Read `tracecat-slackbot-best-practices`.")
    # These are deliberately outside the skill. The host must not import plugin
    # capabilities other than its selected skill directories.
    (vendored / "hooks.json").write_text("{}")
    (vendored / ".mcp.json").write_text("{}")
    plugin = tmp_path / "plugin"

    stage_platform_skill_plugin(
        asset_names=["tracecat-workspace-chat"],
        vendored_root=vendored,
        plugin_root=plugin,
    )

    assert orjson.loads((plugin / ".claude-plugin" / "plugin.json").read_bytes()) == {
        "name": "tracecat"
    }
    staged = plugin / "skills" / "workspace-chat"
    markdown = (staged / "SKILL.md").read_text()
    parsed = parse_skill_markdown(markdown)
    assert parsed is not None
    assert parsed.name == staged.name == "workspace-chat"
    assert parsed.description == "Platform guidance"
    assert "$tracecat:automation-best-practices" in markdown
    assert (
        "`tracecat:slackbot-best-practices`"
        in (staged / "references" / "guide.md").read_text()
    )
    assert not (plugin / "hooks.json").exists()
    assert not (plugin / ".mcp.json").exists()
    assert not (plugin / "skills" / "automation-best-practices").exists()


@pytest.mark.anyio
async def test_workspace_skill_with_same_name_is_staged_independently(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    platform = PLATFORM_SKILLS[0]
    vendored = tmp_path / "vendored"
    source = vendored / platform.asset_name
    source.mkdir(parents=True)
    (source / "SKILL.md").write_text(
        f"---\nname: {platform.asset_name}\n---\nPlatform instructions"
    )
    plugin = tmp_path / "platform-plugin"
    stage_platform_skill_plugin(
        asset_names=[platform.asset_name], vendored_root=vendored, plugin_root=plugin
    )
    cached = tmp_path / "cache"
    cached.mkdir()
    workspace_markdown = (
        f"---\nname: {platform.skill_name}\n---\nWorkspace instructions"
    )
    (cached / "SKILL.md").write_text(workspace_markdown)
    resolved = ResolvedSkillRef(
        skill_id=uuid.uuid4(),
        skill_name=platform.skill_name,
        skill_version_id=uuid.uuid4(),
        manifest_sha256="a" * 64,
    )

    @asynccontextmanager
    async def with_session(*, role: object):
        yield object()

    monkeypatch.setattr(activity_module.SkillService, "with_session", with_session)
    materialize = AsyncMock(return_value=cached)
    executor = SimpleNamespace(
        input=SimpleNamespace(
            config=SimpleNamespace(resolved_skills=[resolved]), role=object()
        ),
        _ensure_cached_skill_dir=materialize,
    )
    workspace_skills = tmp_path / "workspace-skills"
    workspace_skills.mkdir()
    stage = SandboxedAgentExecutor._stage_resolved_skills.__get__(executor)
    await stage(workspace_skills)

    assert resolved.origin is SkillOrigin.WORKSPACE
    assert platform.origin is SkillOrigin.PLATFORM
    assert (
        workspace_skills / platform.skill_name / "SKILL.md"
    ).read_text() == workspace_markdown
    assert (
        "Platform instructions"
        in (plugin / "skills" / platform.skill_name / "SKILL.md").read_text()
    )
    materialize.assert_awaited_once()


def test_workspace_response_cannot_claim_platform_origin() -> None:
    with pytest.raises(ValidationError) as exc:
        SkillReadMinimal.model_validate({"origin": "platform"})
    assert any(error["loc"] == ("origin",) for error in exc.value.errors())


def test_prefixed_workspace_frontmatter_is_valid() -> None:
    parsed = parse_skill_markdown(
        "---\nname: tracecat-triage\n---\nWorkspace instructions"
    )
    assert parsed is not None
    assert parsed.name == "tracecat-triage"


def test_workspace_origin_survives_workflow_serialization() -> None:
    skill = ResolvedSkillRef(
        skill_id=uuid.uuid4(),
        skill_name="tracecat-triage",
        skill_version_id=uuid.uuid4(),
        manifest_sha256="a" * 64,
    )
    config = AgentConfig(
        model_name="synthetic", model_provider="anthropic", resolved_skills=[skill]
    )
    payload = agent_config_to_payload(config)
    assert payload.resolved_skills is not None
    assert payload.resolved_skills[0].model_dump(mode="json")["origin"] == "workspace"
    assert agent_config_from_payload(payload).resolved_skills == [skill]
    with pytest.raises(ValidationError):
        ResolvedSkillRefPayload.model_validate(
            {**payload.resolved_skills[0].model_dump(), "origin": "platform"}
        )
