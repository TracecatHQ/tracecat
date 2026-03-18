from __future__ import annotations

from uuid import uuid4

import pytest

from tracecat.registry.actions.enums import TemplateActionValidationErrorType
from tracecat.registry.actions.schemas import RegistryActionValidationErrorInfo
from tracecat.registry.sync.runner import (
    RegistrySyncRunner,
    RegistrySyncValidationError,
)
from tracecat.registry.sync.schemas import RegistrySyncRequest
from tracecat.registry.sync.tarball import TarballVenvBuildResult


@pytest.mark.anyio
async def test_runner_raises_before_upload_on_validation_errors(
    tmp_path,
    mocker,
) -> None:
    runner = RegistrySyncRunner()
    request = RegistrySyncRequest(
        repository_id=uuid4(),
        origin="tracecat_registry",
        origin_type="builtin",
        validate_actions=True,
    )

    tarball_path = tmp_path / "site-packages.tar.gz"
    tarball_path.write_bytes(b"tarball")

    mocker.patch.object(
        runner,
        "_get_builtin_package_path",
        mocker.AsyncMock(return_value=tmp_path),
    )
    mocker.patch.object(
        runner,
        "_build_tarball_venv",
        mocker.AsyncMock(
            return_value=TarballVenvBuildResult(
                tarball_path=tarball_path,
                tarball_name="site-packages.tar.gz",
                content_hash="hash",
                compressed_size_bytes=7,
            )
        ),
    )
    mocker.patch.object(
        runner,
        "_discover_actions",
        mocker.AsyncMock(
            return_value=(
                [],
                {
                    "tools.example.action": [
                        RegistryActionValidationErrorInfo(
                            type=TemplateActionValidationErrorType.SERIALIZATION_ERROR,
                            details=["Forbidden access to os.environ"],
                            is_template=False,
                            loc_primary="tools.example.action",
                            loc_secondary=None,
                        )
                    ]
                },
            )
        ),
    )
    upload_tarball = mocker.patch.object(
        runner,
        "_upload_tarball",
        mocker.AsyncMock(return_value="s3://registry/tarball.tgz"),
    )

    with pytest.raises(
        RegistrySyncValidationError,
        match="Registry sync validation failed: 1 validation error",
    ):
        await runner.run(request)

    upload_tarball.assert_not_awaited()
