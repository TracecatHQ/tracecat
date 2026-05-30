from __future__ import annotations

import argparse
from pathlib import Path
from types import SimpleNamespace
from typing import cast

import pytest

from scripts.evals.tracecat_authoring import run_local


def test_parse_agents_rejects_explicit_empty_agents() -> None:
    args = argparse.Namespace(agent="codex", agents=" , ")

    with pytest.raises(SystemExit, match="At least one agent"):
        run_local.parse_agents(args)


def test_workflow_ids_from_call_decodes_mcp_text_content() -> None:
    workflow_id = "123e4567-e89b-12d3-a456-426614174000"
    call = run_local.McpToolCall(
        call_id="call_1",
        tool_name="create_workflow",
        payload=[
            {
                "type": "text",
                "text": f'{{"id":"{workflow_id}","title":"Created"}}',
            }
        ],
    )

    assert run_local.workflow_ids_from_call(call) == [workflow_id]


@pytest.mark.anyio
async def test_score_case_fetches_created_and_changed_workflow_snapshots(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    fetched: list[str] = []

    async def fake_fetch_workflow_snapshot(*_args: object, **kwargs: object):
        workflow_id = str(kwargs["workflow_id"])
        fetched.append(workflow_id)
        return run_local.WorkflowSnapshot(
            workflow_id=workflow_id,
            raw={},
            yaml_payload=None,
            definition=None,
            layout={},
        )

    async def fake_fetch_action_schemas(*_args: object, **_kwargs: object):
        return {}

    monkeypatch.setattr(
        run_local, "fetch_workflow_snapshot", fake_fetch_workflow_snapshot
    )
    monkeypatch.setattr(run_local, "fetch_action_schemas", fake_fetch_action_schemas)

    transcript_path = tmp_path / "transcript.jsonl"
    transcript_path.write_text("", encoding="utf-8")
    final_path = tmp_path / "final.json"
    final_path.write_text("{}", encoding="utf-8")

    await run_local.score_case(
        cast(run_local.TracecatMCP, SimpleNamespace()),
        case=run_local.Case(id="case", prompt="", rubric=[]),
        agent="codex",
        workspace_id="workspace",
        final_response={
            "workflow_ids": ["created", "overlap"],
            "changed_workflow_ids": ["overlap", "changed"],
        },
        transcript_path=transcript_path,
        final_path=final_path,
        seed_workflow_id=None,
        duration_seconds=0.0,
    )

    assert fetched == ["created", "overlap", "changed"]


def test_prompt_action_literal_check_rejects_unknown_core_action() -> None:
    result = run_local.validate_prompt_action_literals("Use `core.noop` here.")

    assert not result.passed
    assert "core.noop" in result.detail


def test_prompt_action_literal_check_rejects_unknown_ai_action() -> None:
    result = run_local.validate_prompt_action_literals("Use `ai.make_things_up` here.")

    assert not result.passed
    assert "ai.make_things_up" in result.detail


def test_prompt_action_literal_check_rejects_concrete_tools_action() -> None:
    result = run_local.validate_prompt_action_literals(
        "Use `tools.slack.post_message`."
    )

    assert not result.passed
    assert "tools.slack.post_message" in result.detail


def test_prompt_action_literal_check_allows_placeholder_tools_syntax() -> None:
    result = run_local.validate_prompt_action_literals(
        "Use `tools.<integration_slug>.<action_name>` after discovery."
    )

    assert result.passed


def test_prompt_facing_sources_stay_within_named_budgets() -> None:
    results = run_local.validate_prompt_source_budgets(
        run_local.prompt_facing_sources()
    )

    assert all(result.passed for result in results), [
        result.detail for result in results if not result.passed
    ]


def test_prompt_action_signature_coverage_spans_all_prompt_sources() -> None:
    results = run_local.validate_prompt_action_signatures(
        run_local.combine_prompt_sources(run_local.prompt_facing_sources())
    )
    coverage = next(
        result
        for result in results
        if result.name == "prompt_action_signature_coverage"
    )

    assert coverage.passed, coverage.detail


def test_prompt_facing_sources_prefer_run_python_over_workflow_fanout() -> None:
    result = run_local.validate_prompt_loop_parallelism_guardrails(
        run_local.combine_prompt_sources(run_local.prompt_facing_sources())
    )

    assert result.passed, result.detail


def test_prompt_facing_sources_include_mcp_tool_argument_guardrails() -> None:
    result = run_local.validate_prompt_mcp_tool_argument_guardrails(
        run_local.combine_prompt_sources(run_local.prompt_facing_sources())
    )

    assert result.passed, result.detail


def test_prompt_fenced_block_validation_covers_all_prompt_sources() -> None:
    sources = [
        run_local.PromptSource("mcp_instructions", ""),
        run_local.PromptSource(
            "dsl_reference",
            "```yaml\nactions:\n  - ref: transform\n    action: core.transform.reshape\n    args:\n      value: {}\n```",
        ),
        run_local.PromptSource(
            "best_practices_skill",
            '```json\n{"patch_ops": [{"op": "add", "path": "/definition/actions/-", "value": {}}]}\n```',
        ),
    ]

    results = run_local.validate_prompt_fenced_blocks(sources)

    assert any(
        result.name == "dsl_reference:fence_1_yaml:yaml_parse" and result.passed
        for result in results
    )
    assert any(
        result.name == "best_practices_skill:fence_1_json:json_parse" and result.passed
        for result in results
    )


def test_prompt_table_upsert_check_rejects_unsafe_upsert_examples() -> None:
    result = run_local.validate_prompt_table_upsert_examples(
        [
            run_local.PromptSource(
                "example",
                "```python\nawait insert_rows(table='t', rows_data=rows, upsert=True)\n```",
            )
        ]
    )

    assert not result.passed
    assert "example:2" in result.detail


def test_prompt_table_upsert_check_allows_unique_index_guidance() -> None:
    result = run_local.validate_prompt_table_upsert_examples(
        [
            run_local.PromptSource(
                "example",
                "Create a unique index on external_id first.\n"
                "```python\n"
                "await insert_rows(table='t', rows_data=rows, upsert=True)\n"
                "```",
            )
        ]
    )

    assert result.passed


def test_prompt_facing_sources_do_not_show_unsafe_upsert_examples() -> None:
    result = run_local.validate_prompt_table_upsert_examples(
        run_local.prompt_facing_sources()
    )

    assert result.passed, result.detail
