import pytest
from pydantic import ValidationError
from tracecat_ee.tunnels.schemas import TunnelCreate, TunnelTarget, TunnelUpdate


def _create(**overrides: object) -> TunnelCreate:
    params: dict[str, object] = {
        "name": "onprem-splunk",
        "endpoint": "https://pangolin.example.com/",
        "client_id": "client-123",
        "client_secret": "s3cr3t",
        "allowed_targets": [{"host": "splunk.corp.example", "ports": [8089]}],
    }
    params.update(overrides)
    return TunnelCreate.model_validate(params)


def test_create_defaults_to_pangolin_and_masks_secret() -> None:
    params = _create()
    assert params.provider == "pangolin"
    assert "s3cr3t" not in repr(params)
    assert params.client_secret.get_secret_value() == "s3cr3t"


@pytest.mark.parametrize(
    "endpoint",
    [
        "http://pangolin.example.com",
        "https://user:pass@pangolin.example.com",
        "https://pangolin.example.com/?next=x",
    ],
)
def test_create_rejects_unsafe_endpoints(endpoint: str) -> None:
    with pytest.raises(ValidationError):
        _create(endpoint=endpoint)


@pytest.mark.parametrize(
    "name", ["", "Onprem", "-splunk", "splunk-", "on prem", "a" * 64]
)
def test_create_rejects_invalid_names(name: str) -> None:
    with pytest.raises(ValidationError):
        _create(name=name)


def test_create_rejects_workspace_id_in_body() -> None:
    with pytest.raises(ValidationError):
        _create(workspace_id="00000000-0000-0000-0000-000000000000")


@pytest.mark.parametrize(
    "target",
    [
        {"host": "*.corp.example", "ports": [443]},
        {"host": "10.0.0.0/8", "ports": [443]},
        {"host": "splunk.corp.example", "ports": []},
        {"host": "splunk.corp.example", "ports": [0]},
        {"host": "splunk.corp.example", "ports": [65536]},
        {"host": "splunk.corp.example", "ports": [8089, 8089]},
        {"host": "splunk.corp.example:8089", "ports": [8089]},
    ],
)
def test_target_rejects_broad_or_invalid_values(target: dict[str, object]) -> None:
    with pytest.raises(ValidationError):
        TunnelTarget.model_validate(target)


def test_target_normalizes_host_and_ports() -> None:
    target = TunnelTarget.model_validate(
        {"host": "Splunk.Corp.Example.", "ports": [8443, 8089]}
    )
    assert target.host == "splunk.corp.example"
    assert target.ports == [8089, 8443]
    assert TunnelTarget(host="10.1.2.3", ports=[22]).host == "10.1.2.3"


def test_create_requires_targets_and_unique_hosts() -> None:
    with pytest.raises(ValidationError):
        _create(allowed_targets=[])
    with pytest.raises(ValidationError):
        _create(
            allowed_targets=[
                {"host": "splunk.corp.example", "ports": [8089]},
                {"host": "splunk.corp.example", "ports": [443]},
            ]
        )


@pytest.mark.parametrize(
    "field", ["name", "endpoint", "client_id", "client_secret", "allowed_targets"]
)
def test_update_rejects_explicit_null(field: str) -> None:
    with pytest.raises(ValidationError):
        TunnelUpdate.model_validate({field: None})


def test_update_allows_clearing_description() -> None:
    params = TunnelUpdate.model_validate({"description": None})
    assert params.model_fields_set == {"description"}
