import base64
import subprocess
from pathlib import Path
from types import SimpleNamespace
from typing import Any

import kubernetes.config  # type: ignore
import orjson
import pytest
from tracecat_registry._internal.kubernetes import decode_kubeconfig
from tracecat_registry.integrations import kubernetes as k8s
from yaml import safe_dump, safe_load

from tests.shared import get_package_path
from tracecat.registry.actions.models import TemplateAction

VALID_CONFIG_DICT: dict[str, Any] = {
    "apiVersion": "v1",
    "clusters": [
        {
            "cluster": {"server": "https://example.com"},
            "name": "test",
        }
    ],
    "contexts": [
        {
            "context": {"cluster": "test", "user": "test-user"},
            "name": "test-context",
        }
    ],
    "users": [
        {"name": "test-user", "user": {"token": "test-token"}},
    ],
}

VALID_CONFIG_B64: str = (
    base64.b64encode(safe_dump(VALID_CONFIG_DICT).encode()).decode().rstrip("=")
)


@pytest.fixture
def k8s_templates_dir() -> Path:
    import tracecat_registry

    return get_package_path(tracecat_registry) / "templates" / "tools" / "kubernetes"


@pytest.fixture(autouse=True)
def patch_kubeconfig(monkeypatch: pytest.MonkeyPatch) -> None:  # noqa: PT004
    """Patch helpers that access the local environment."""
    monkeypatch.setattr(
        kubernetes.config, "load_kube_config_from_dict", lambda *_, **__: None
    )
    monkeypatch.setattr(
        k8s, "secrets", SimpleNamespace(get=lambda _key: VALID_CONFIG_B64)
    )
    monkeypatch.setattr(k8s, "validate_namespace", lambda _ns: None)


def test_decode_kubeconfig_returns_yaml() -> None:  # noqa: D103
    yaml_str = decode_kubeconfig(VALID_CONFIG_B64)
    assert isinstance(yaml_str, str)

    parsed = safe_load(yaml_str)
    assert parsed == VALID_CONFIG_DICT


def test_decode_kubeconfig_invalid_empty() -> None:  # noqa: D103
    empty_b64 = base64.b64encode(b"{}").decode().rstrip("=")
    with pytest.raises(ValueError, match="kubeconfig cannot be empty"):
        decode_kubeconfig(empty_b64)


def _fake_proc(stdout: str = "", returncode: int = 0):  # noqa: D401
    """Return a CompletedProcess-like object suitable for monkeypatching."""
    return subprocess.CompletedProcess(
        args=["kubectl"], returncode=returncode, stdout=stdout, stderr=""
    )


def test_run_command_json_output(monkeypatch: pytest.MonkeyPatch) -> None:  # noqa: D103
    expected: dict[str, Any] = {"kind": "PodList", "items": []}
    monkeypatch.setattr(
        subprocess, "run", lambda *_, **__: _fake_proc(orjson.dumps(expected).decode())
    )

    result = k8s.run_command(command="get pods", namespace="test-ns", dry_run=True)
    assert result == expected


def test_run_command_error(monkeypatch: pytest.MonkeyPatch) -> None:  # noqa: D103
    monkeypatch.setattr(
        subprocess, "run", lambda *_, **__: _fake_proc("boom", returncode=1)
    )

    with pytest.raises(RuntimeError, match="kubectl failed"):
        k8s.run_command(command="get pods", namespace="test-ns")


def test_run_command_non_json_output(monkeypatch: pytest.MonkeyPatch) -> None:  # noqa: D103
    """Test commands that don't support JSON output return raw stdout."""
    expected_output = "hello world"
    monkeypatch.setattr(subprocess, "run", lambda *_, **__: _fake_proc(expected_output))

    result = k8s.run_command(
        command=["exec", "test-pod", "--", "echo", "hello"], namespace="test-ns"
    )
    assert result == expected_output


def test_run_command_with_stdin(monkeypatch: pytest.MonkeyPatch) -> None:  # noqa: D103
    """Test passing stdin to kubectl command."""
    manifest = {"apiVersion": "v1", "kind": "Pod", "metadata": {"name": "test"}}
    expected_response = {"kind": "Pod", "metadata": {"name": "test", "uid": "123"}}

    def mock_run(*args, **kwargs):
        assert kwargs.get("input") == orjson.dumps(manifest).decode()
        return _fake_proc(orjson.dumps(expected_response).decode())

    monkeypatch.setattr(subprocess, "run", mock_run)

    result = k8s.run_command(
        command=["create", "-f", "-"],
        namespace="test-ns",
        stdin=orjson.dumps(manifest).decode(),
    )
    assert result == expected_response


def test_run_command_dry_run(monkeypatch: pytest.MonkeyPatch) -> None:  # noqa: D103
    """Test dry-run flag is properly passed to kubectl."""

    def mock_run(*args, **kwargs):
        assert "--dry-run=client" in args[0]
        return _fake_proc("{}")

    monkeypatch.setattr(subprocess, "run", mock_run)

    k8s.run_command(command="get pods", namespace="test-ns", dry_run=True)


# Template validation tests


def test_kubernetes_template_parsing(k8s_templates_dir: Path) -> None:
    """Test that all Kubernetes templates can be parsed correctly."""

    for template_path in k8s_templates_dir.glob("*.yml"):
        # This will raise if parsing fails
        action = TemplateAction.from_yaml(template_path)
        assert action.type == "action"
        assert action.definition
        assert action.definition.namespace == "tools.kubernetes"
        assert action.definition.name == template_path.stem


def test_list_pods_template_structure(k8s_templates_dir: Path) -> None:
    """Test the structure of the list_pods template."""

    template_path = k8s_templates_dir / "list_pods.yml"
    action = TemplateAction.from_yaml(template_path)

    # Verify template structure
    assert action.definition.name == "list_pods"
    assert action.definition.namespace == "tools.kubernetes"
    assert "namespace" in action.definition.expects
    assert len(action.definition.steps) == 1
    assert action.definition.steps[0].action == "tools.kubernetes.run_command"


def test_create_template_structure(k8s_templates_dir: Path) -> None:
    """Test the structure of the create template."""

    template_path = k8s_templates_dir / "create.yml"
    action = TemplateAction.from_yaml(template_path)

    # Verify template structure
    assert action.definition.name == "create"
    assert "manifest" in action.definition.expects
    assert "namespace" in action.definition.expects
    assert "dry_run" in action.definition.expects

    # Check that stdin is properly configured
    step = action.definition.steps[0]
    assert step.action == "tools.kubernetes.run_command"
    assert "stdin" in step.args
