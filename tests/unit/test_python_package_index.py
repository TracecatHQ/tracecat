"""Organization Python package index settings and sandbox install wiring."""

import uuid
from pathlib import Path
from unittest.mock import AsyncMock, patch

import pytest
from pydantic import ValidationError
from sqlalchemy.ext.asyncio import AsyncSession

from tracecat.auth.types import Role
from tracecat.contexts import ctx_role
from tracecat.sandbox import unsafe_pid_executor
from tracecat.sandbox.executor import NsjailExecutor
from tracecat.sandbox.service import SandboxService
from tracecat.sandbox.types import PackageIndex, SandboxConfig
from tracecat.sandbox.unsafe_pid_executor import UnsafePidExecutor
from tracecat.sandbox.wrapper import INSTALL_SCRIPT
from tracecat.settings import service as settings_service_module
from tracecat.settings.router import get_python_package_settings
from tracecat.settings.schemas import PythonPackageSettingsUpdate
from tracecat.settings.service import (
    PythonPackageIndexNotAllowedError,
    SettingsService,
    get_python_package_index,
)

INDEX_URL = "https://example.jfrog.io/artifactory/api/pypi/pypi-virtual/simple"


@pytest.mark.parametrize(
    ("url", "message"),
    [
        ("http://example.jfrog.io/simple", "must use https"),
        ("file:///etc/passwd", "must use https"),
        ("https://user:token@example.jfrog.io/simple", "must not embed credentials"),
        ("https://example.jfrog.io/simple?token=x", "query string or fragment"),
        ("https:///simple", "must include a hostname"),
        ("https://example.jfrog.io/sim\nple", "control characters"),
    ],
)
def test_update_rejects_unsafe_urls(url: str, message: str) -> None:
    with pytest.raises(ValidationError, match=message):
        PythonPackageSettingsUpdate(python_package_index_url=url)


def test_update_blank_values_clear_settings() -> None:
    params = PythonPackageSettingsUpdate(
        python_package_index_url="  ",
        python_package_index_username="",
        python_package_index_password="",
    )
    assert params.python_package_index_url is None
    assert params.python_package_index_username is None
    assert params.python_package_index_password is None


def test_package_index_install_env_keeps_credentials_out_of_repr() -> None:
    index = PackageIndex(url=INDEX_URL, username="svc@corp", password="p@ss:/word")

    env = index.install_env()

    assert env["UV_INDEX_URL"] == (
        "https://svc%40corp:p%40ss%3A%2Fword@example.jfrog.io"
        "/artifactory/api/pypi/pypi-virtual/simple"
    )
    assert env["UV_NO_BUILD"] == "1"
    assert "p@ss" not in repr(index)
    assert "word" not in index.cache_fingerprint


def test_package_index_without_credentials_allows_source_builds() -> None:
    assert PackageIndex(url=INDEX_URL).install_env() == {"UV_INDEX_URL": INDEX_URL}


def test_nsjail_install_env_prefers_org_index(tmp_path: Path) -> None:
    executor = NsjailExecutor(
        rootfs_path=str(tmp_path / "rootfs"), cache_dir=str(tmp_path / "cache")
    )
    index = PackageIndex(url=INDEX_URL, username="u", password="secret")
    with patch(
        "tracecat.sandbox.executor.TRACECAT__SANDBOX_PYPI_EXTRA_INDEX_URLS",
        ["https://extra.example.com/simple"],
    ):
        install_env = executor._build_env_map(
            SandboxConfig(package_index=index), "install"
        )
        default_env = executor._build_env_map(SandboxConfig(), "install")
        execute_env = executor._build_env_map(
            SandboxConfig(package_index=index), "execute"
        )

    assert install_env["UV_INDEX_URL"] == index.install_env()["UV_INDEX_URL"]
    assert install_env["UV_NO_BUILD"] == "1"
    assert "UV_EXTRA_INDEX_URL" not in install_env
    assert default_env["UV_INDEX_URL"] == "https://pypi.org/simple"
    assert default_env["UV_EXTRA_INDEX_URL"] == "https://extra.example.com/simple"
    assert not any("secret" in value for value in execute_env.values())


def test_install_script_ends_option_parsing_before_dependencies() -> None:
    assert 'cmd.append("--")\ncmd.extend(deps)' in INSTALL_SCRIPT


def test_cache_key_is_scoped_by_index_but_unchanged_by_default(tmp_path: Path) -> None:
    service = SandboxService.__new__(SandboxService)
    deps = ["requests==2.32.3"]
    default_key = service._compute_cache_key(deps, "ws")
    index_key = service._compute_cache_key(deps, "ws", PackageIndex(url=INDEX_URL))
    rotated_key = service._compute_cache_key(
        deps, "ws", PackageIndex(url=INDEX_URL, password="new-token")
    )
    other_user_key = service._compute_cache_key(
        deps, "ws", PackageIndex(url=INDEX_URL, username="other")
    )

    assert default_key == service._compute_cache_key(deps, "ws", None)
    assert index_key != default_key
    assert rotated_key == index_key
    assert other_user_key != index_key

    direct = UnsafePidExecutor(cache_dir=str(tmp_path))
    assert direct._compute_cache_key(deps, "ws") == default_key
    assert direct._compute_cache_key(deps, "ws", PackageIndex(url=INDEX_URL)) == (
        index_key
    )


@pytest.mark.anyio
async def test_direct_install_passes_credentials_via_env_not_argv(
    tmp_path: Path,
) -> None:
    executor = UnsafePidExecutor(cache_dir=str(tmp_path))
    process = AsyncMock()
    process.returncode = 0
    create_process = AsyncMock(return_value=process)
    index = PackageIndex(url=INDEX_URL, username="u", password="secret-token")

    with (
        patch.object(
            unsafe_pid_executor.asyncio, "create_subprocess_exec", create_process
        ),
        patch.object(
            unsafe_pid_executor,
            "communicate_process_group",
            AsyncMock(return_value=(b"", b"")),
        ),
    ):
        await executor._install_packages(
            tmp_path / "venv", ["--index-url=https://evil.example"], package_index=index
        )

    await_args = create_process.await_args
    assert await_args is not None
    argv = list(await_args.args)
    assert not any("secret-token" in arg for arg in argv)
    assert argv[-2:] == ["--", "--index-url=https://evil.example"]
    assert await_args.kwargs["env"]["UV_INDEX_URL"].startswith(
        "https://u:secret-token@"
    )


@pytest.fixture
def allow_public_index(monkeypatch: pytest.MonkeyPatch) -> None:
    async def _noop(url: str) -> None:
        return None

    monkeypatch.setattr(
        settings_service_module, "validate_url_resolves_public_async", _noop
    )


@pytest.mark.anyio
@pytest.mark.usefixtures("db")
async def test_password_is_encrypted_and_never_returned(
    session: AsyncSession, svc_admin_role: Role, allow_public_index: None
) -> None:
    service = SettingsService(session=session, role=svc_admin_role)
    await service.update_python_package_settings(
        PythonPackageSettingsUpdate(
            python_package_index_url=INDEX_URL,
            python_package_index_username="svc",
            python_package_index_password="secret-token",
        )
    )

    stored = await service.get_org_setting("python_package_index_password")
    assert stored is not None
    assert stored.is_encrypted is True
    assert b"secret-token" not in stored.value

    token = ctx_role.set(svc_admin_role)
    try:
        read = await get_python_package_settings(role=svc_admin_role, session=session)
    finally:
        ctx_role.reset(token)
    assert read.model_dump() == {
        "python_package_index_url": INDEX_URL,
        "python_package_index_username": "svc",
        "python_package_index_password_configured": True,
    }
    assert "secret-token" not in read.model_dump_json()


@pytest.mark.anyio
@pytest.mark.usefixtures("db")
async def test_changing_url_clears_saved_password(
    session: AsyncSession, svc_admin_role: Role, allow_public_index: None
) -> None:
    service = SettingsService(session=session, role=svc_admin_role)
    await service.update_python_package_settings(
        PythonPackageSettingsUpdate(
            python_package_index_url=INDEX_URL,
            python_package_index_password="secret-token",
        )
    )
    # Username-only update on the same URL keeps the password.
    await service.update_python_package_settings(
        PythonPackageSettingsUpdate(python_package_index_username="svc")
    )
    stored = await service.get_org_setting("python_package_index_password")
    assert stored is not None and service.get_value(stored) == "secret-token"

    await service.update_python_package_settings(
        PythonPackageSettingsUpdate(
            python_package_index_url="https://attacker.example.com/simple"
        )
    )
    stored = await service.get_org_setting("python_package_index_password")
    assert stored is not None and service.get_value(stored) is None


@pytest.mark.anyio
@pytest.mark.usefixtures("db")
async def test_rejects_url_resolving_to_private_address(
    session: AsyncSession, svc_admin_role: Role, monkeypatch: pytest.MonkeyPatch
) -> None:
    from tracecat.network import DisallowedUrlError

    async def _reject(url: str) -> None:
        raise DisallowedUrlError("non-public")

    monkeypatch.setattr(
        settings_service_module, "validate_url_resolves_public_async", _reject
    )
    service = SettingsService(session=session, role=svc_admin_role)
    with pytest.raises(PythonPackageIndexNotAllowedError):
        await service.update_python_package_settings(
            PythonPackageSettingsUpdate(
                python_package_index_url="https://metadata.internal/simple"
            )
        )
    assert await service.get_org_setting("python_package_index_url") is None


@pytest.mark.anyio
@pytest.mark.usefixtures("db")
async def test_runtime_lookup_is_scoped_to_role_organization(
    session: AsyncSession, svc_admin_role: Role, allow_public_index: None
) -> None:
    service = SettingsService(session=session, role=svc_admin_role)
    await service.update_python_package_settings(
        PythonPackageSettingsUpdate(
            python_package_index_url=INDEX_URL,
            python_package_index_password="secret-token",
        )
    )

    index = await get_python_package_index(role=svc_admin_role, session=session)
    assert index == PackageIndex(url=INDEX_URL, password="secret-token")

    other_org_role = svc_admin_role.model_copy(update={"organization_id": uuid.uuid4()})
    assert await get_python_package_index(role=other_org_role, session=session) is None
