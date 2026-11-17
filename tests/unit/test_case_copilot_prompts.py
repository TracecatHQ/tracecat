import json
import uuid
from datetime import UTC, datetime
from typing import Any

import pytest
import yaml

from tracecat.cases.enums import CasePriority, CaseSeverity, CaseStatus
from tracecat.cases.prompts import MAX_CASE_PROMPT_DATA_CHARS, CaseCopilotPrompts
from tracecat.db.models import Case


def _make_case(payload: dict[str, Any] | None) -> Case:
    now = datetime.now(UTC)
    return Case(
        id=uuid.uuid4(),
        workspace_id=uuid.uuid4(),
        case_number=42,
        summary="Suspicious login",
        description="Multiple failed logins followed by a success.",
        priority=CasePriority.HIGH,
        severity=CaseSeverity.HIGH,
        status=CaseStatus.NEW,
        payload=payload,
        created_at=now,
        updated_at=now,
    )


def test_small_case_payload_is_embedded_in_full() -> None:
    case = _make_case({"source_ip": "198.51.100.7", "user": "alice"})

    instructions = CaseCopilotPrompts(case=case).instructions

    assert "source_ip: 198.51.100.7" in instructions
    assert "PayloadPreview" not in instructions
    assert str(case.id) in instructions


@pytest.mark.parametrize("payload_chars", [MAX_CASE_PROMPT_DATA_CHARS * 3, 2_000_000])
def test_large_case_payload_is_truncated(payload_chars: int) -> None:
    case = _make_case({"raw_event": "A" * payload_chars, "tail_marker": "END"})

    instructions = CaseCopilotPrompts(case=case).instructions

    assert len(instructions) < MAX_CASE_PROMPT_DATA_CHARS + 5_000
    assert '<PayloadPreview truncated="true"' in instructions
    total_chars = len(json.dumps(case.payload))
    assert f'total_chars="{total_chars}"' in instructions
    assert "core.cases.get_case" in instructions
    assert "END" not in instructions
    assert "Suspicious login" in instructions
    assert str(case.id) in instructions


def test_payload_that_fits_after_split_is_not_marked_truncated(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    nested: dict[str, Any] = {"leaf": "value"}
    for _ in range(60):
        nested = {"node": nested}
    case = _make_case(nested)
    case_dict = case.to_dict()
    full_yaml = yaml.dump(case_dict, indent=2)
    case_dict.pop("payload")
    budget = len(yaml.dump(case_dict, indent=2)) + len(json.dumps(case.payload))
    assert budget < len(full_yaml)
    monkeypatch.setattr("tracecat.cases.prompts.MAX_CASE_PROMPT_DATA_CHARS", budget)

    instructions = CaseCopilotPrompts(case=case).instructions

    assert "<Payload>" in instructions
    assert "PayloadPreview" not in instructions
    assert json.dumps(case.payload) in instructions
