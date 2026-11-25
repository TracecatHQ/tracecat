"""Contract tests for the Push Security registry templates.

The control writes are read-modify-write: Push replaces a control's whole
configuration on PUT, so the Python step that builds the new configuration must
keep every rule and setting it was not asked to change.
"""

from pathlib import Path
from typing import Any

import pytest
import yaml
from tracecat_registry import RegistrySecret

from tracecat.registry.actions.schemas import TemplateAction

TEMPLATE_DIR = (
    Path(__file__).parents[2]
    / "packages/tracecat-registry/tracecat_registry/templates/tools/push_security"
)
TEMPLATES = sorted(TEMPLATE_DIR.rglob("*.yml"))
BASE_URL = "${{ inputs.base_url || VARS.push_security.base_url }}/v1/"
WRITE_METHODS = {"POST", "PUT", "PATCH"}
CONTROLS = {
    "accountConditionEnforcement",
    "appBanner",
    "appBannerUrlPattern",
    "browserExtensionBlocking",
    "clipboardBlocking",
    "clonedLoginPageDetection",
    "domainCategoryBlocking",
    "fileDownloadBlocking",
    "fileUploadBlocking",
    "mfaEnforcement",
    "maliciousBrowserExtensionDetection",
    "maliciousCopyPasteDetection",
    "passwordLoggingPrevention",
    "phishingToolDetection",
    "strongPasswordEnforcement",
}


def _ids(path: Path) -> str:
    return path.stem


def _main(name: str) -> Any:
    path = next(p for p in TEMPLATES if p.stem == name)
    steps = yaml.safe_load(path.read_text())["definition"]["steps"]
    script = next(s for s in steps if s["action"] == "core.script.run_python")
    namespace: dict[str, Any] = {}
    exec(script["args"]["script"], namespace)
    return namespace["main"]


def _config() -> dict[str, Any]:
    return {
        "globals": {"ignoredHosts": ["intranet.example.com"]},
        "rules": [
            {"id": "r1", "name": "First", "enabled": True, "mode": "MONITOR"},
            {"id": "r2", "name": "Second", "enabled": True, "mode": "BLOCK"},
        ],
    }


def test_templates_exist() -> None:
    assert len(TEMPLATES) == 36


@pytest.mark.parametrize("path", TEMPLATES, ids=_ids)
def test_template_contract(path: Path) -> None:
    definition = TemplateAction.from_yaml(path).definition
    assert definition.namespace == "tools.push_security"
    assert definition.secrets == [
        RegistrySecret(name="push_security", keys=["PUSH_SECURITY_API_KEY"])
    ]
    assert "base_url" in definition.expects
    http_steps = [s for s in definition.steps if s.action == "core.http_request"]
    assert http_steps
    for step in http_steps:
        # No default region: a key only works against its own regional host.
        assert step.args["url"].startswith(BASE_URL)
        assert (
            step.args["headers"]["x-api-key"]
            == "${{ SECRETS.push_security.PUSH_SECURITY_API_KEY }}"
        )
        # Bulk deletes and irreversible deletes are out of scope.
        assert step.args["method"] != "DELETE"
    if any("/configuration" in s.args["url"] for s in http_steps):
        # Control PUTs replace the whole configuration, so read it first.
        methods = [s.args["method"] for s in http_steps]
        assert methods in (["GET"], ["GET", "PUT"])
    assert definition.returns == f"${{{{ steps.{definition.steps[-1].ref}.result }}}}"


def test_control_input_lists_every_control() -> None:
    for path in TEMPLATES:
        expects = yaml.safe_load(path.read_text())["definition"]["expects"]
        if "control" in expects:
            assert set(expects["control"]["enum"]) == CONTROLS


def test_add_control_rule_keeps_existing_configuration() -> None:
    main = _main("add_control_rule")
    new = {"name": "New", "enabled": True, "mode": "BLOCK"}
    appended = main(_config(), new, None)
    assert appended["globals"] == _config()["globals"]
    assert [r.get("id") for r in appended["rules"]] == ["r1", "r2", None]
    first = main(_config(), new, 0)
    assert [r["name"] for r in first["rules"]] == ["New", "First", "Second"]


def test_update_control_rule_merges_fields_and_moves_rule() -> None:
    main = _main("update_control_rule")
    updated = main(_config(), "r2", {"enabled": False}, None)
    assert updated["globals"] == _config()["globals"]
    assert updated["rules"][0] == _config()["rules"][0]
    assert updated["rules"][1] == {**_config()["rules"][1], "enabled": False}
    moved = main(_config(), "r2", {}, 0)
    assert [r["id"] for r in moved["rules"]] == ["r2", "r1"]
    with pytest.raises(ValueError):
        main(_config(), "missing", {"enabled": False}, None)


def test_remove_control_rule_removes_only_matching_id() -> None:
    main = _main("remove_control_rule")
    removed = main(_config(), "r1")
    assert removed == {
        "globals": _config()["globals"],
        "rules": [_config()["rules"][1]],
    }
    with pytest.raises(ValueError):
        main(_config(), "missing")


def test_update_control_globals_keeps_rules() -> None:
    main = _main("update_control_globals")
    updated = main(_config(), {"ignoredHosts": []})
    assert updated == {"globals": {"ignoredHosts": []}, "rules": _config()["rules"]}


def test_block_browser_extension_reuses_managed_rule() -> None:
    main = _main("block_browser_extension")
    name, title, subtext = "Blocked by Tracecat", "Blocked", "Contact IT."
    created = main(_config(), ["ext-a"], name, title, subtext)
    assert [r.get("id") for r in created["rules"]] == [None, "r1", "r2"]
    managed = created["rules"][0]
    assert managed["mode"] == "BLOCK"
    assert managed["criteria"]["browserExtensions"] == {
        "matches": ["ext-a"],
        "action": "INCLUDE",
    }
    managed["id"] = "managed"
    again = main(created, ["ext-a", "ext-b"], name, title, subtext)
    assert len(again["rules"]) == 3
    assert again["rules"][0]["id"] == "managed"
    assert again["rules"][0]["criteria"]["browserExtensions"]["matches"] == [
        "ext-a",
        "ext-b",
    ]


def test_block_app_sets_banner_fields() -> None:
    main = _main("block_app")
    blocked = main(
        {"rules": []}, ["SLACK"], "Blocked by Tracecat", "t", "s", True, "Ask"
    )
    rule = blocked["rules"][0]
    assert rule["criteria"]["appTypes"]["matches"] == ["SLACK"]
    assert rule["allowReasonSubmission"] is True
    assert rule["buttonText"] == "Ask"
