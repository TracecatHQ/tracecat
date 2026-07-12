"""Tests for workflow-safe agent configuration conversion."""

import uuid

from tracecat.agent.skill.types import ResolvedSkillRef
from tracecat.agent.types import AgentConfig
from tracecat.agent.workflow_config import (
    agent_config_from_payload,
    agent_config_to_payload,
)
from tracecat.agent.workflow_schemas import AgentConfigPayload


def test_resolved_skill_slug_uses_legacy_temporal_wire_key() -> None:
    """New workers stay compatible with old histories and old strict readers."""

    skill_id = uuid.uuid4()
    skill_version_id = uuid.uuid4()
    legacy_payload = AgentConfigPayload.model_validate(
        {
            "model_name": "gpt-4o-mini",
            "model_provider": "openai",
            "retries": 3,
            "resolved_skills": [
                {
                    "skill_id": str(skill_id),
                    "skill_name": "package-slug",
                    "skill_version_id": str(skill_version_id),
                    "manifest_sha256": "a" * 64,
                }
            ],
        }
    )

    restored = agent_config_from_payload(legacy_payload)
    assert restored.resolved_skills == [
        ResolvedSkillRef(
            skill_id=skill_id,
            skill_slug="package-slug",
            skill_version_id=skill_version_id,
            manifest_sha256="a" * 64,
        )
    ]

    round_trip = agent_config_to_payload(
        AgentConfig(
            model_name="gpt-4o-mini",
            model_provider="openai",
            resolved_skills=restored.resolved_skills,
        )
    ).model_dump(mode="json")
    assert round_trip["resolved_skills"] == [
        {
            "skill_id": str(skill_id),
            "skill_name": "package-slug",
            "skill_version_id": str(skill_version_id),
            "manifest_sha256": "a" * 64,
        }
    ]
