from __future__ import annotations

import ast
from pathlib import Path

from scripts.generate_mcp_docs import (
    _clean_docstring,
    _decorator_description,
    _module_string_constants,
)
from tracecat.cases.enums import CaseEventType


def test_update_case_trigger_docs_include_every_event_type() -> None:
    root = Path(__file__).resolve().parents[2]
    source = (root / "tracecat" / "mcp" / "server.py").read_text(encoding="utf-8")
    module = ast.parse(source)
    constants = _module_string_constants(module)
    tool = next(
        node
        for node in module.body
        if isinstance(node, ast.AsyncFunctionDef) and node.name == "update_case_trigger"
    )

    description = _clean_docstring(_decorator_description(tool, constants))

    assert "omitting `status` fails" in description
    assert "{_CASE_EVENT_TYPE_VALUES_CSV}" not in description
    for event_type in CaseEventType:
        assert event_type.value in description
