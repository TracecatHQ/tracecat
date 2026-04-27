import json
import re
from typing import cast

from tracecat_ee.spm.schemas import (
    SpmControlContext,
    SpmControlResult,
    SpmSkillControlData,
)

_RISK_RULES: list[tuple[str, re.Pattern[str]]] = [
    (
        "prompt_injection_language",
        re.compile(
            r"\b(ignore previous|system prompt|override policy|bypass guardrail)\b",
            re.I,
        ),
    ),
    (
        "credential_collection_language",
        re.compile(
            r"\b(cookie|credential|token|secret|api[_-]?key|aws_secret_access_key)\b",
            re.I,
        ),
    ),
    (
        "remote_execution_language",
        re.compile(r"\b(curl|wget|ssh|osascript|rm -rf|bash -c|python -c)\b", re.I),
    ),
]


def check(ctx: SpmControlContext) -> SpmControlResult:
    asset = cast(SpmSkillControlData, ctx.asset)
    serialized_skill = json.dumps(asset.skill, sort_keys=True, default=str)
    text = "\n".join(filter(None, [asset.name or "", serialized_skill]))
    matches = [rule_id for rule_id, pattern in _RISK_RULES if pattern.search(text)]

    return SpmControlResult(
        failed=asset.parse_status == "ok" and bool(matches),
        summary=f"{asset.display_name} matches risky skill heuristics",
        evidence={
            "fingerprint": asset.fingerprint,
            "name": asset.name,
            "matched_rules": matches,
            "parse_status": asset.parse_status,
        },
        recommended_payload={
            "fingerprint": asset.fingerprint,
            "name": asset.name,
            "target_path": asset.file_path,
        },
    )
