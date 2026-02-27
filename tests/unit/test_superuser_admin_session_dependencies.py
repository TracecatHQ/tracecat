from __future__ import annotations

import ast
from pathlib import Path

SUPERUSER_ADMIN_ROUTERS = (
    Path("tracecat/admin/registry/router.py"),
    Path("packages/tracecat-ee/tracecat_ee/admin/organizations/router.py"),
    Path("packages/tracecat-ee/tracecat_ee/admin/users/router.py"),
    Path("packages/tracecat-ee/tracecat_ee/admin/settings/router.py"),
    Path("packages/tracecat-ee/tracecat_ee/admin/tiers/router.py"),
)


def _annotation_names(node: ast.AsyncFunctionDef) -> set[str]:
    names: set[str] = set()
    args = (
        *node.args.posonlyargs,
        *node.args.args,
        *node.args.kwonlyargs,
    )
    for arg in args:
        annotation = arg.annotation
        if isinstance(annotation, ast.Name):
            names.add(annotation.id)
    return names


def test_superuser_routes_use_bypass_session_dependency() -> None:
    violations: list[str] = []

    for router_path in SUPERUSER_ADMIN_ROUTERS:
        module = ast.parse(router_path.read_text())
        for node in ast.walk(module):
            if not isinstance(node, ast.AsyncFunctionDef):
                continue

            annotation_names = _annotation_names(node)
            if "SuperuserRole" not in annotation_names:
                continue
            if "AsyncDBSession" in annotation_names:
                violations.append(f"{router_path}:{node.lineno}")

    assert not violations, (
        "Superuser routes must use AsyncDBSessionBypass instead of AsyncDBSession:\n"
        + "\n".join(violations)
    )
