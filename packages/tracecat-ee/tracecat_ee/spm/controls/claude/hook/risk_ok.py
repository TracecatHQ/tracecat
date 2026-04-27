import re
from typing import cast

from tracecat_ee.spm.schemas import (
    SpmControlContext,
    SpmControlResult,
    SpmHookControlData,
)

_RISK_RULES: list[tuple[str, re.Pattern[str]]] = [
    (
        "remote_exec_pipeline",
        re.compile(r"(curl|wget).{0,120}(\||&&|;).{0,40}\b(sh|bash|zsh)\b", re.I),
    ),
    (
        "inline_interpreter_exec",
        re.compile(r"\b(bash|sh|zsh|python|python3|node|osascript)\s+-c\b", re.I),
    ),
    (
        "credential_material_reference",
        re.compile(
            r"\b(api[_-]?key|access[_-]?token|secret[_-]?key|aws_secret_access_key)\b",
            re.I,
        ),
    ),
]


def check(ctx: SpmControlContext) -> SpmControlResult:
    asset = cast(SpmHookControlData, ctx.asset)
    matches = [
        rule_id
        for rule_id, pattern in _RISK_RULES
        if asset.command and pattern.search(asset.command)
    ]
    return SpmControlResult(
        failed=asset.parse_status == "ok" and bool(matches),
        summary=f"{asset.display_name} matches risky hook heuristics",
        evidence={
            "fingerprint": asset.fingerprint,
            "event": asset.event,
            "command": asset.command,
            "matched_rules": matches,
            "parse_status": asset.parse_status,
        },
        recommended_payload={
            "fingerprint": asset.fingerprint,
            "target_path": asset.file_path,
        },
    )
