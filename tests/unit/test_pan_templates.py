"""Contract and planner tests for the Palo Alto SCM and PAN-OS templates."""

from pathlib import Path
from typing import Any

import pytest
import yaml
from tracecat_registry import RegistryOAuthSecret, RegistrySecret

from tracecat.registry.actions.schemas import TemplateAction

TEMPLATES = (
    Path(__file__).resolve().parents[2]
    / "packages"
    / "tracecat-registry"
    / "tracecat_registry"
    / "templates"
    / "tools"
)
SCM_TEMPLATES = sorted((TEMPLATES / "pan_strata").rglob("*.yml"))
PANOS_TEMPLATES = sorted((TEMPLATES / "pan_os").rglob("*.yml"))
SCM_BASE_URL = '${{ inputs.base_url || VARS.pan_strata.base_url || "https://api.strata.paloaltonetworks.com" }}'


def _ids(path: Path) -> str:
    return str(path.relative_to(TEMPLATES))


def _steps(path: Path) -> list[dict[str, Any]]:
    return yaml.safe_load(path.read_text())["definition"]["steps"]


def _script(rel: str, ref: str) -> Any:
    steps = _steps(TEMPLATES / f"{rel}.yml")
    step = next(step for step in steps if step["ref"] == ref)
    namespace: dict[str, Any] = {}
    exec(compile(step["args"]["script"], rel, "exec"), namespace)
    return namespace["main"]


def test_templates_exist() -> None:
    assert len(SCM_TEMPLATES) >= 40
    assert len(PANOS_TEMPLATES) >= 45


@pytest.mark.parametrize("path", SCM_TEMPLATES, ids=_ids)
def test_scm_template_contract(path: Path) -> None:
    definition = TemplateAction.from_yaml(path).definition
    assert definition.namespace == "tools.pan_strata"
    assert definition.secrets == [
        RegistryOAuthSecret(provider_id="pan_strata", grant_type="client_credentials")
    ]
    assert "base_url" in definition.expects
    for step in definition.steps:
        if step.action.startswith("core.http_"):
            # Endpoints must stay configurable for FedRAMP tenants.
            assert step.args["url"].startswith(SCM_BASE_URL)
            assert (
                step.args["headers"]["Authorization"]
                == "Bearer ${{ SECRETS.pan_strata_oauth.PAN_STRATA_SERVICE_TOKEN }}"
            )
            # Only the explicit push action may push the candidate configuration.
            if definition.name != "push_candidate_config":
                assert "candidate:push" not in step.args["url"]


@pytest.mark.parametrize("path", PANOS_TEMPLATES, ids=_ids)
def test_panos_template_contract(path: Path) -> None:
    definition = TemplateAction.from_yaml(path).definition
    assert definition.namespace == "tools.pan_os"
    assert definition.secrets == [RegistrySecret(name="pan_os", keys=["PANOS_API_KEY"])]
    assert {"base_url", "verify_ssl"} <= set(definition.expects)
    for step in definition.steps:
        if step.action.startswith("core.http_"):
            assert step.args["url"].startswith(
                "${{ inputs.base_url || VARS.pan_os.base_url }}"
            )
            assert (
                step.args["headers"]["X-PAN-KEY"]
                == "${{ SECRETS.pan_os.PANOS_API_KEY }}"
            )
            assert step.args["verify_ssl"] == "${{ inputs.verify_ssl }}"


@pytest.mark.parametrize("path", SCM_TEMPLATES + PANOS_TEMPLATES, ids=_ids)
def test_python_steps_compile(path: Path) -> None:
    for step in _steps(path):
        if step["action"] == "core.script.run_python":
            compile(step["args"]["script"], str(path), "exec")


@pytest.mark.parametrize(
    ("kind", "value", "expected"),
    [
        (
            "ip",
            " 203.0.113.10 ",
            ("ip_netmask", "203.0.113.10/32", "tc-block-203.0.113.10"),
        ),
        (
            "ip",
            "2001:db8::1",
            ("ip_netmask", "2001:db8::1/128", "tc-block-2001-db8--1"),
        ),
        (
            "cidr",
            "198.51.100.7/24",
            ("ip_netmask", "198.51.100.0/24", "tc-block-198.51.100.0_24"),
        ),
        (
            "ip_or_cidr",
            "203.0.113.10",
            ("ip_netmask", "203.0.113.10/32", "tc-block-203.0.113.10"),
        ),
        (
            "fqdn",
            "Evil.Example.com.",
            ("fqdn", "evil.example.com", "tc-block-evil.example.com"),
        ),
    ],
)
def test_scm_block_indicator_normalization(
    kind: str, value: str, expected: tuple[str, str, str]
) -> None:
    plan = _script("pan_strata/containment/block_ip", "plan")
    result = plan(
        kind=kind,
        value=value,
        name_prefix="tc-block-",
        address_name=None,
        folder="Shared",
        snippet=None,
        device=None,
    )
    indicator = result["indicator"]
    assert (indicator["field"], indicator["value"], indicator["name"]) == expected
    assert result["address_params"] == {"folder": "Shared", "name": expected[2]}


@pytest.mark.parametrize(
    ("kind", "value", "error"),
    [
        ("ip", "10.0.0.0/8", "does not appear to be an IPv4 or IPv6 address"),
        ("fqdn", "not a domain", "Invalid FQDN"),
    ],
)
def test_scm_block_rejects_invalid_indicators(
    kind: str, value: str, error: str
) -> None:
    plan = _script("pan_strata/containment/block_ip", "plan")
    with pytest.raises(ValueError, match=error):
        plan(
            kind=kind,
            value=value,
            name_prefix="tc-block-",
            address_name=None,
            folder="Shared",
            snippet=None,
            device=None,
        )


def test_scm_block_is_idempotent() -> None:
    plan_address = _script("pan_strata/containment/block_ip", "plan_address")
    plan_group = _script("pan_strata/containment/block_ip", "plan_group")
    indicator = {
        "field": "ip_netmask",
        "value": "203.0.113.10/32",
        "name": "tc-block-203.0.113.10",
    }
    existing = {
        "status_code": 200,
        "data": {"id": "a1", "name": indicator["name"], "ip_netmask": "203.0.113.10"},
    }
    assert (
        plan_address(existing, indicator, {"folder": "Shared"}, None, None)["method"]
        == "GET"
    )

    collision = {"status_code": 200, "data": {"id": "a1", "ip_netmask": "10.0.0.1/32"}}
    with pytest.raises(ValueError, match="already exists"):
        plan_address(collision, indicator, {"folder": "Shared"}, None, None)

    missing = {"status_code": 404, "data": {"_errors": []}}
    created = plan_address(missing, indicator, {"folder": "Shared"}, "case-1", None)
    assert created["method"] == "POST"
    assert created["payload"] == {
        "name": indicator["name"],
        "ip_netmask": "203.0.113.10/32",
        "folder": "Shared",
        "description": "case-1",
    }

    group = {"id": "g1", "name": "blk", "folder": "Shared", "static": ["placeholder"]}
    added = plan_group(
        {"status_code": 200, "data": group}, "blk", indicator["name"], True
    )
    assert added["method"] == "PUT"
    assert added["payload"] == {
        "name": "blk",
        "folder": "Shared",
        "static": ["placeholder", indicator["name"]],
    }

    group["static"].append(indicator["name"])
    noop = plan_group(
        {"status_code": 200, "data": group}, "blk", indicator["name"], False
    )
    assert noop["method"] == "GET" and noop["already_blocked"]


def test_scm_unblock_refuses_to_empty_static_group() -> None:
    plan_group = _script("pan_strata/containment/unblock_ip", "plan_group")
    group = {"id": "g1", "name": "blk", "static": ["tc-block-203.0.113.10"]}
    with pytest.raises(ValueError, match="empty"):
        plan_group({"status_code": 200, "data": group}, "blk", "tc-block-203.0.113.10")


def test_panos_group_update_keeps_location_attributes() -> None:
    plan_group = _script("pan_os/containment/block_ip", "plan_group")
    response = {
        "status_code": 200,
        "data": {
            "result": {
                "entry": [
                    {
                        "@name": "blk",
                        "@location": "vsys",
                        "@vsys": "vsys1",
                        "static": {"member": "placeholder"},
                    }
                ]
            }
        },
    }
    plan = plan_group(response, "blk", "tc-block-203.0.113.10", True)
    assert plan["method"] == "PUT"
    assert plan["payload"] == {
        "entry": {
            "@name": "blk",
            "@location": "vsys",
            "@vsys": "vsys1",
            "static": {"member": ["placeholder", "tc-block-203.0.113.10"]},
        }
    }


def test_panorama_commit_all_limits_devices() -> None:
    build = _script("pan_os/operations/commit_all", "build_command")
    form = build(
        device_groups=["DG <1>"],
        devices=["0071"],
        description=None,
        include_template=False,
        force_template_values=True,
    )
    assert form["type"] == "commit" and form["action"] == "all"
    assert form["cmd"] == (
        "<commit-all><shared-policy><device-group>"
        '<entry name="DG &lt;1&gt;"><devices><entry name="0071"/></devices></entry>'
        "</device-group><force-template-values>yes</force-template-values>"
        "</shared-policy></commit-all>"
    )
    with pytest.raises(ValueError, match="at least one device group"):
        build(
            device_groups=[],
            devices=None,
            description=None,
            include_template=False,
            force_template_values=False,
        )


def test_panorama_push_template_stack_command() -> None:
    build = _script("pan_os/panorama/push_template_stack", "build_command")
    form = build(
        tag="template-stack",
        name="TS & 1",
        devices=["0071", "0072"],
        description="IR-1",
        force_template_values=False,
    )
    assert form["cmd"] == (
        "<commit-all><template-stack><name>TS &amp; 1</name>"
        "<description>IR-1</description>"
        "<device><member>0071</member><member>0072</member></device>"
        "</template-stack></commit-all>"
    )


def test_panos_xml_commands_escape_user_input() -> None:
    build = _script("pan_os/operations/commit", "build_command")
    form = build(description="case <1> & more", admins=["admin"])
    assert form["cmd"] == (
        "<commit><partial><admin><member>admin</member></admin></partial>"
        "<description>case &lt;1&gt; &amp; more</description></commit>"
    )
    register = _script("pan_os/user_id/register_ip_tags", "build_command")
    form = register(
        ips=["203.0.113.10"],
        tags=["tc<block>"],
        timeout=3600,
        persistent=True,
        vsys=None,
        target="0079",
    )
    assert form["target"] == "0079"
    assert '<member timeout="3600">tc&lt;block&gt;</member>' in form["cmd"]
    with pytest.raises(ValueError):
        register(
            ips=["not-an-ip"],
            tags=["x"],
            timeout=None,
            persistent=True,
            vsys=None,
            target=None,
        )


def test_panos_job_parser() -> None:
    parse = _script("pan_os/operations/wait_for_job", "parse")
    text = (
        '<response status="success"><result><job><id>5</id><type>Commit</type>'
        "<status>FIN</status><result>FAIL</result><details><line>bad config</line>"
        "</details></job></result></response>"
    )
    with pytest.raises(ValueError, match="bad config"):
        parse(text=text, job_id="5", raise_on_failure=True)
    job = parse(text=text, job_id="5", raise_on_failure=False)
    assert job["done"] and not job["succeeded"] and job["details"] == ["bad config"]
