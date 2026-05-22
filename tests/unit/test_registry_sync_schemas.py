from tracecat.registry.sync.schemas import RegistrySyncResult


def test_registry_sync_result_uses_artifact_uri_attribute() -> None:
    result = RegistrySyncResult(
        artifact_uri="s3://registry/platform/site-packages.squashfs"
    )

    assert result.artifact_uri == "s3://registry/platform/site-packages.squashfs"
    assert result.tarball_uri == result.artifact_uri
    assert (
        result.model_dump(exclude_unset=True)["tarball_uri"]
        == "s3://registry/platform/site-packages.squashfs"
    )
    assert "artifact_uri" not in result.model_dump(exclude_unset=True)
    assert (
        result.model_dump(by_alias=False)["artifact_uri"]
        == "s3://registry/platform/site-packages.squashfs"
    )
    assert "tarball_uri" not in result.model_dump(by_alias=False)


def test_registry_sync_result_accepts_legacy_tarball_uri() -> None:
    result = RegistrySyncResult.model_validate(
        {"tarball_uri": "s3://registry/platform/site-packages.tar.gz"}
    )

    assert result.artifact_uri == "s3://registry/platform/site-packages.tar.gz"
    assert result.tarball_uri == result.artifact_uri
