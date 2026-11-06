"""Guards for the `auth_mode` input on templates that accept both OAuth grants.

A template that declares user and service tokens for a provider exposes
`auth_mode` so a workflow can pick the connection: `delegated` resolves only
user tokens, `application` only service tokens, and `auto` keeps the
template's original fallback chain.
"""

import re
from collections.abc import Iterator
from pathlib import Path
from typing import Any

import pytest

from tracecat.expressions.eval import eval_templated_object
from tracecat.registry.actions.schemas import TemplateAction

TEMPLATES = (
    Path(__file__).resolve().parents[2]
    / "packages"
    / "tracecat-registry"
    / "tracecat_registry"
    / "templates"
    / "tools"
)
SECRET_REF = re.compile(r"SECRETS\.([a-z_]+)\.([A-Z_]+)")
CHAIN = re.compile(
    r"SECRETS\.[a-z_]+_oauth\.[A-Z_]+_(?:USER|SERVICE)_TOKEN"
    r" \|\| SECRETS\.[a-z_]+_oauth\.[A-Z_]+_(?:USER|SERVICE)_TOKEN"
)


def _strings(value: Any) -> Iterator[str]:
    if isinstance(value, str):
        yield value
    elif isinstance(value, dict):
        for item in value.values():
            yield from _strings(item)
    elif isinstance(value, list):
        for item in value:
            yield from _strings(item)


def _resolve(template: str, secrets: dict[str, dict[str, str]], mode: str) -> Any:
    return eval_templated_object(
        template, operand={"SECRETS": secrets, "inputs": {"auth_mode": mode}}
    )


def _mixed_grant_templates() -> list[Path]:
    paths = []
    for path in sorted(TEMPLATES.rglob("*.yml")):
        raw = path.read_text()
        if (
            CHAIN.search(raw)
            and re.search(r"_oauth\.[A-Z_]+_USER_TOKEN", raw)
            and re.search(r"_oauth\.[A-Z_]+_SERVICE_TOKEN", raw)
        ):
            paths.append(path)
    return paths


MIXED_GRANT_TEMPLATES = _mixed_grant_templates()


def test_mixed_grant_templates_found() -> None:
    assert len(MIXED_GRANT_TEMPLATES) > 300


@pytest.mark.parametrize(
    "path", MIXED_GRANT_TEMPLATES, ids=lambda p: str(p.relative_to(TEMPLATES))
)
def test_auth_mode_selects_grant(path: Path) -> None:
    definition = TemplateAction.from_yaml(path).definition
    auth_mode = definition.expects["auth_mode"]
    assert auth_mode.type == 'enum["auto", "delegated", "application"]'
    assert auth_mode.default == "auto"

    exprs = [
        s
        for step in definition.steps
        for s in _strings(step.args)
        if "inputs.auth_mode" in s
    ]
    assert exprs

    for expr in exprs:
        refs = SECRET_REF.findall(expr)
        secrets: dict[str, dict[str, str]] = {}
        for name, key in refs:
            secrets.setdefault(name, {})[key] = f"<{name}.{key}>"
        fallback_chain = (
            expr[: expr.index("${{")] + "${{ " + expr.rsplit(" else ", 1)[1]
        )

        delegated = _resolve(expr, secrets, "delegated")
        application = _resolve(expr, secrets, "application")
        assert "_USER_TOKEN>" in delegated
        assert "_SERVICE_TOKEN>" not in delegated
        assert "_SERVICE_TOKEN>" in application
        assert "_USER_TOKEN>" not in application
        assert _resolve(expr, secrets, "auto") == _resolve(
            fallback_chain, secrets, "auto"
        )
