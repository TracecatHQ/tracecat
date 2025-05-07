import base64
import subprocess
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
import yaml
from pydantic import SecretStr
from tracecat_registry.integrations.ee.kubernetes import (
    get_logs,
    list_containers,
    list_pods,
    list_pvc,
    list_secrets,
)

from tracecat.ee.sandbox.kubernetes import _decode_kubeconfig
from tracecat.executor.service import run_template_action
from tracecat.registry.actions.models import TemplateAction
from tracecat.registry.repository import Repository
from tracecat.secrets.models import SecretKeyValue


@pytest.fixture(autouse=True)
def kubernetes_repo():
    """Initialize repository with core actions."""
    repo = Repository()
    repo.init(include_base=True, include_templates=False)
    return repo


@pytest.fixture(autouse=True)
def load_kubernetes_templates(kubernetes_repo):
    """Load the kubernetes template actions."""
    templates_dir = Path("registry/tracecat_registry/templates/ee/kubernetes")
    for template_path in templates_dir.glob("*.yml"):
        with open(template_path) as f:
            template = yaml.safe_load(f)
        action = TemplateAction.model_validate(template)
        kubernetes_repo.register_template_action(action)


@pytest.fixture(autouse=True)
def current_kubeconfig():
    """Get a kubernetes config from the current user's kubeconfig file.

    Assumes that the current user has a kubeconfig file at ~/.kube/config.

    Returns:
        str: Base64 encoded kubernetes config
    """
    process = subprocess.run(
        ["kubectl", "config", "view", "--minify", "--flatten"],
        capture_output=True,
        text=True,
        check=True,
    )
    kubeconfig_yaml = process.stdout
    kubeconfig_base64 = base64.b64encode(kubeconfig_yaml.encode()).decode().rstrip("=")

    return kubeconfig_base64


@pytest.fixture(autouse=True, scope="session")
def tracecat_ci_namespace():
    """Create a test namespace."""
    try:
        subprocess.run(
            ["kubectl", "create", "ns", "tracecat-ci"],
            check=True,
        )
    except subprocess.CalledProcessError:
        pass
    yield
    try:
        subprocess.run(
            ["kubectl", "delete", "ns", "tracecat-ci"],
            check=True,
        )
    except subprocess.CalledProcessError:
        pass


@pytest.fixture(autouse=True, scope="session")
def test_pod(tracecat_ci_namespace):
    """Create a test pod. alpine:latest that sleeps for 10 minutes."""
    try:
        subprocess.run(
            [
                "kubectl",
                "run",
                "test-pod",
                "-n",
                "tracecat-ci",
                "--image=alpine:latest",
                "--command",
                "--",
                "sh",
                "-c",
                'while true; do echo "log message $(date)"; sleep 600; done',
            ],
            check=True,
        )
    except subprocess.CalledProcessError:
        pass
    yield
    try:
        subprocess.run(
            ["kubectl", "delete", "pod", "test-pod", "-n", "tracecat-ci"],
            check=True,
        )
    except subprocess.CalledProcessError:
        pass


def test_decode_kubeconfig_as_dict(current_kubeconfig):
    """Test decoding kubeconfig to dictionary."""
    decoded = _decode_kubeconfig(current_kubeconfig)

    assert isinstance(decoded, dict)
    assert decoded["apiVersion"] == "v1"
    assert len(decoded["clusters"]) == 1
    assert decoded["clusters"][0]["name"] in [
        "kind-tracecat-ci",
        "kubernetes",
        "orbstack",
    ]
    assert len(decoded["contexts"]) == 1
    assert decoded["contexts"][0]["name"] in [
        "kind-tracecat-ci",
        "kubernetes",
        "orbstack",
    ]


def test_decode_kubeconfig_as_yaml(current_kubeconfig):
    """Test decoding kubeconfig to YAML string."""
    decoded = _decode_kubeconfig(current_kubeconfig, as_yaml=True)

    assert isinstance(decoded, str)
    # Parse the YAML to verify it's valid
    parsed = yaml.safe_load(decoded)
    assert parsed["apiVersion"] == "v1"
    assert len(parsed["clusters"]) == 1
    assert parsed["clusters"][0]["name"] in [
        "kind-tracecat-ci",
        "kubernetes",
        "orbstack",
    ]
    assert len(parsed["contexts"]) == 1
    assert parsed["contexts"][0]["name"] in [
        "kind-tracecat-ci",
        "kubernetes",
        "orbstack",
    ]


def test_decode_kubeconfig_invalid_empty():
    """Test decoding with empty kubeconfig raises ValueError."""
    empty_config = base64.b64encode(b"{}").decode().rstrip("=")

    with pytest.raises(ValueError, match="kubeconfig cannot be empty"):
        _decode_kubeconfig(empty_config)


def test_decode_kubeconfig_invalid_no_contexts():
    """Test decoding with no contexts raises ValueError."""
    no_contexts = {
        "apiVersion": "v1",
        "clusters": [{"cluster": {"server": "https://example.com"}, "name": "test"}],
        "users": [{"name": "test-user", "user": {"token": "test-token"}}],
    }
    no_contexts_base64 = (
        base64.b64encode(yaml.dump(no_contexts).encode()).decode().rstrip("=")
    )

    with pytest.raises(
        ValueError, match="kubeconfig must contain at least one context"
    ):
        _decode_kubeconfig(no_contexts_base64)


def test_decode_kubeconfig_invalid_default_namespace():
    """Test decoding with default namespace raises ValueError."""
    default_ns = {
        "apiVersion": "v1",
        "clusters": [{"cluster": {"server": "https://example.com"}, "name": "test"}],
        "contexts": [
            {
                "context": {"cluster": "test", "user": "test-user"},
                "name": "test-context",
                "namespace": "default",
            }
        ],
        "users": [{"name": "test-user", "user": {"token": "test-token"}}],
    }
    default_ns_base64 = (
        base64.b64encode(yaml.dump(default_ns).encode()).decode().rstrip("=")
    )

    with pytest.raises(ValueError, match="kubeconfig cannot contain default namespace"):
        _decode_kubeconfig(default_ns_base64)


@pytest.fixture
def mock_validate_access():
    """Mock the Kubernetes access validation."""
    with patch(
        "tracecat.ee.sandbox.kubernetes._validate_access", return_value=None
    ) as mock:
        yield mock


@pytest.fixture
def mock_secret_service(mocker, current_kubeconfig):
    """Mock the SecretsService methods needed for Kubernetes tests."""
    # Mock get_secret_by_name to return a dummy Secret object
    get_secret_mock = mocker.patch(
        "tracecat.secrets.service.SecretsService.get_secret_by_name",
        new_callable=AsyncMock,
        return_value=MagicMock(),
    )

    # Mock decrypt_keys to return a list with the kubeconfig
    decrypt_keys_mock = mocker.patch(
        "tracecat.secrets.service.SecretsService.decrypt_keys",
        return_value=[
            SecretKeyValue(key="kubeconfig", value=SecretStr(current_kubeconfig))
        ],
    )

    # Mock the secrets.get method in tracecat_registry to return the mock kubeconfig
    registry_secrets_mock = mocker.patch(
        "tracecat_registry.secrets.get", return_value=current_kubeconfig
    )

    # Return a dict with all mocks for tests that need direct access
    return {
        "get_secret_by_name": get_secret_mock,
        "decrypt_keys": decrypt_keys_mock,
        "registry_secrets": registry_secrets_mock,
    }


# Kubectl Template Actions


@pytest.mark.anyio
async def test_create_job(kubernetes_repo, mock_secret_service):
    """Test the kubernetes create job template action with dry run."""

    # Get the registered action
    bound_action = kubernetes_repo.get("ee.kubernetes.create")

    # Test create alpine pod
    test_args = {
        "manifest": {
            "apiVersion": "batch/v1",
            "kind": "Job",
            "metadata": {"name": "test-job"},
            "spec": {
                "template": {
                    "spec": {
                        "containers": [
                            {
                                "name": "alpine",
                                "image": "alpine:latest",
                                "command": ["sleep", "3600"],
                            }
                        ]
                    }
                }
            },
        },
        "namespace": "test-ns",
        "dry_run": True,
    }

    # Run the action
    result = await run_template_action(
        action=bound_action,
        args=test_args,
        context={},
    )

    # Basic validation of the result
    assert isinstance(result, dict)
    assert "stdout" in result
    assert "stderr" in result
    assert "returncode" in result
    assert result["returncode"] == 0, result["stderr"]


@pytest.mark.anyio
async def test_delete_job(kubernetes_repo, mock_secret_service):
    """Test the kubernetes delete job template action."""

    # Get the registered delete action
    bound_action = kubernetes_repo.get("ee.kubernetes.delete")

    # Test delete job
    test_args = {
        "name": "test-job",
        "resource": "job",  # Use "resource" instead of "kind"
        "namespace": "test-ns",
        "dry_run": True,  # Use dry_run for testing
    }

    # Run the action
    result = await run_template_action(
        action=bound_action,
        args=test_args,
        context={},
    )

    # Validate results
    assert isinstance(result, dict)
    assert "stdout" in result
    assert "stderr" in result
    assert "returncode" in result
    assert result["returncode"] == 0, result["stderr"]


@pytest.mark.anyio
async def test_create_pvc(kubernetes_repo, mock_secret_service):
    """Test the kubernetes create pvc template action."""

    # Get the registered action
    bound_action = kubernetes_repo.get("ee.kubernetes.create_pvc")

    # Test create pvc
    test_args = {
        "name": "test-pvc",
        "namespace": "test-ns",
        "size": "1Gi",
        "access_modes": ["ReadWriteOnce"],
        "storage_class": "standard",
        "dry_run": True,
    }

    # Run the action
    result = await run_template_action(
        action=bound_action,
        args=test_args,
        context={},
    )

    # Validate results
    assert isinstance(result, dict)
    assert "stdout" in result
    assert "stderr" in result
    assert "returncode" in result
    assert result["returncode"] == 0


@pytest.mark.anyio
async def test_create_secret(kubernetes_repo, mock_secret_service):
    """Test the kubernetes create secret template action."""

    # Get the registered action
    bound_action = kubernetes_repo.get("ee.kubernetes.create_secret")

    # Test create secret
    test_args = {
        "name": "test-secret",
        "namespace": "test-ns",
        "data": {"key": "value"},
        "dry_run": True,
    }

    # Run the action
    result = await run_template_action(
        action=bound_action,
        args=test_args,
        context={},
    )

    # Validate results
    assert isinstance(result, dict)
    assert "stdout" in result
    assert "stderr" in result
    assert "returncode" in result
    assert result["returncode"] == 0, result["stderr"]


@pytest.mark.anyio
async def test_run_image(kubernetes_repo, mock_secret_service):
    """Test the kubernetes run image template action with dry run."""

    # Get the registered action
    bound_action = kubernetes_repo.get("ee.kubernetes.run")

    # Test run pod
    test_args = {
        "pod": "test-pod",
        "image": "alpine:latest",
        "command": ["echo", "hello"],
        "namespace": "test-ns",
        "dry_run": True,
    }

    # Run the action
    result = await run_template_action(
        action=bound_action,
        args=test_args,
        context={},
    )

    # Validate results
    assert isinstance(result, dict)
    assert "stdout" in result
    assert "stderr" in result
    assert "returncode" in result
    assert result["returncode"] == 0, result["stderr"]


@pytest.mark.anyio
async def test_exec(kubernetes_repo, mock_secret_service, test_pod):
    """Test the kubernetes exec command template action."""

    # Get the registered action
    bound_action = kubernetes_repo.get("ee.kubernetes.exec")

    # Test exec command
    test_args = {
        "pod": "test-pod",
        "command": ["echo", "hello", "world!"],
        "namespace": "tracecat-ci",
    }

    # Run the action
    result = await run_template_action(
        action=bound_action,
        args=test_args,
        context={},
    )

    # Validate results
    assert isinstance(result, dict)
    assert "stdout" in result
    assert "stderr" in result
    assert "returncode" in result
    assert result["returncode"] == 0, result["stderr"]
    assert "hello" in result["stdout"], result["stdout"]


### UDFs


def test_get_logs(kubernetes_repo, mock_secret_service, test_pod):
    """Test the kubernetes get logs template action."""

    # Get the logs
    result = get_logs(
        pod="test-pod",
        namespace="tracecat-ci",
    )
    assert isinstance(result, str)
    assert "log message" in result


def test_list_containers(kubernetes_repo, mock_secret_service, test_pod):
    """Test the kubernetes list containers template action."""
    result = list_containers(
        pod="test-pod",
        namespace="tracecat-ci",
    )
    assert isinstance(result, list)


def test_list_pods(kubernetes_repo, mock_secret_service):
    """Test the kubernetes list pods template action."""
    result = list_pods(
        namespace="tracecat-ci",
    )
    assert isinstance(result, list)


def test_list_pvc(kubernetes_repo, mock_secret_service):
    """Test the kubernetes list pvc template action."""
    result = list_pvc(
        namespace="tracecat-ci",
    )
    assert isinstance(result, list)


def test_list_secrets(kubernetes_repo, mock_secret_service):
    """Test the kubernetes list secrets template action."""
    result = list_secrets(
        namespace="tracecat-ci",
    )
    assert isinstance(result, list)
