import sys
from pathlib import Path

import pytest

from tracecat.exceptions import (
    RegistryError,
    RegistryModuleImportError,
    RegistrySyncContentError,
    RegistryTemplateLoadError,
)
from tracecat.registry.repository import import_and_reload
from tracecat.registry.sync.schemas import (
    RegistrySyncResult,
    SyncErrorCode,
    SyncResultError,
)


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


@pytest.mark.parametrize(
    ("exc", "expected_code"),
    [
        (
            ModuleNotFoundError("No module named 'internal_registry'"),
            SyncErrorCode.PACKAGE_NOT_FOUND,
        ),
        (
            RegistryTemplateLoadError("Failed to load template action from a.yml: bad"),
            SyncErrorCode.TEMPLATE_LOAD_FAILED,
        ),
        (
            ImportError(
                "cannot import name '_ExtendedAttributes' from "
                "'opentelemetry.util.types' (/pythonpath/1/opentelemetry/util/types.py)"
            ),
            SyncErrorCode.IMPORT_FAILED,
        ),
        (RegistryError("Sync subprocess timed out after 300.0s"), None),
        (RuntimeError("git clone failed"), None),
    ],
)
def test_sync_result_error_from_exception_classifies_by_type(
    exc: BaseException, expected_code: SyncErrorCode | None
) -> None:
    result = SyncResultError.from_exception(exc)

    assert result.error == str(exc)
    assert result.error_code == expected_code


def test_sync_result_error_to_exception_maps_code_to_content_error() -> None:
    typed = SyncResultError(
        error="No module named 'internal_registry'",
        error_code=SyncErrorCode.PACKAGE_NOT_FOUND,
    ).to_exception()
    assert isinstance(typed, RegistrySyncContentError)
    assert typed.code == SyncErrorCode.PACKAGE_NOT_FOUND
    assert str(typed) == "No module named 'internal_registry'"

    plain = SyncResultError(error="boom").to_exception()
    assert type(plain) is RegistryError
    assert str(plain) == "boom"


def test_sync_result_error_round_trips_error_code_through_json() -> None:
    original = SyncResultError(
        error="Failed to load template action from a.yml: bad",
        error_code=SyncErrorCode.TEMPLATE_LOAD_FAILED,
    )

    restored = SyncResultError.model_validate_json(original.model_dump_json())

    assert restored == original


def test_attribute_error_during_registry_import_is_import_failure(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    module_name = "synthetic_registry_attribute_conflict"
    (tmp_path / f"{module_name}.py").write_text(
        "import types\ntypes.helper_removed_in_this_version\n"
    )
    monkeypatch.syspath_prepend(str(tmp_path))
    monkeypatch.delitem(sys.modules, module_name, raising=False)

    with pytest.raises(RegistryModuleImportError) as exc_info:
        import_and_reload(module_name)

    result = SyncResultError.from_exception(exc_info.value)
    assert result.error_code == SyncErrorCode.IMPORT_FAILED
    assert "AttributeError while importing" in result.error
