from __future__ import annotations

import json
from pathlib import Path

from tracecat_benchmark.viewer import build_run_list_item


def test_run_list_uses_human_readable_matrix_case_id(tmp_path: Path) -> None:
    (tmp_path / "scenario.json").write_text(
        json.dumps(
            {
                "case_id": "pgdog-1k-exec3-pool12",
                "load_type": "scatter",
                "workflow_count": 4,
                "branch_count": 256,
                "one_shot": True,
            }
        ),
        encoding="utf-8",
    )
    (tmp_path / "manifest.json").write_text(
        json.dumps(
            {
                "case_id": "collector-fallback",
                "status": "completed",
                "started_at": "2026-07-31T00:00:00+00:00",
            }
        ),
        encoding="utf-8",
    )

    item = build_run_list_item(
        tmp_path,
        "sha256:" + ("0" * 64),
        "matrix-20260731T000000Z-abcdef",
    )

    assert item["case_id"] == "pgdog-1k-exec3-pool12"
