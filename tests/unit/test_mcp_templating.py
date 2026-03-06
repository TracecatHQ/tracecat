from __future__ import annotations

from typing import Any

from tracecat.integrations.mcp_templating import eval_mcp_templated_object


def test_eval_mcp_templated_object_evaluates_inline_templates() -> None:
    operand: dict[str, Any] = {
        "VARS": {"api_config": {"base_url": "https://example.com"}}
    }

    result = eval_mcp_templated_object(
        {"url": "prefix-${{ VARS.api_config.base_url }}/v1"},
        operand=operand,
    )

    assert result == {"url": "prefix-https://example.com/v1"}


def test_eval_mcp_templated_object_stringifies_template_keys() -> None:
    metadata = {"region": "us-east-1"}
    operand: dict[str, Any] = {"VARS": {"metadata": metadata}}

    result = eval_mcp_templated_object(
        {"${{ VARS.metadata }}": "value"},
        operand=operand,
    )

    assert result == {str(metadata): "value"}
