"""Tests for RegistryActionsService.sync_actions_from_repository.

This test suite validates:
1. Consistency of syncing actions to the RegistryActions table
2. Handling of create/update/delete operations during sync
3. Error handling when malformed functions cannot be imported
"""

from __future__ import annotations

from pathlib import Path
from textwrap import dedent

import pytest
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from tracecat import config
from tracecat.auth.types import Role
from tracecat.db.models import RegistryAction
from tracecat.exceptions import RegistryError
from tracecat.registry.actions.service import RegistryActionsService
from tracecat.registry.constants import DEFAULT_LOCAL_REGISTRY_ORIGIN
from tracecat.registry.repositories.schemas import RegistryRepositoryCreate
from tracecat.registry.repositories.service import RegistryReposService

pytestmark = pytest.mark.usefixtures("db")


@pytest.fixture
def basic_udf_content() -> str:
    """Sample UDF content for testing."""
    return dedent(
        """
        from tracecat_registry import registry

        @registry.register(
            default_title="Add two numbers",
            namespace="test",
            description="A test action that adds two numbers",
        )
        def add_numbers(a: int, b: int) -> int:
            '''Add two numbers together.'''
            return a + b
        """
    )


@pytest.fixture
def updated_udf_content() -> str:
    """Updated UDF content for testing updates."""
    return dedent(
        """
        from tracecat_registry import registry

        @registry.register(
            default_title="Add three numbers",
            namespace="test",
            description="A test action that adds three numbers",
        )
        def add_numbers(a: int, b: int, c: int = 0) -> int:
            '''Add three numbers together.'''
            return a + b + c
        """
    )


@pytest.fixture
def malformed_udf_content() -> str:
    """Malformed UDF that will fail to import."""
    return dedent(
        """
        from tracecat_registry import registry

        # This import will fail
        from nonexistent_module import nonexistent_function

        @registry.register(
            default_title="Malformed action",
            namespace="test",
            description="This action will fail to import",
        )
        def malformed_action(x: int) -> int:
            return nonexistent_function(x)
        """
    )


@pytest.fixture
async def local_package_path(tmp_path: Path, basic_udf_content: str) -> Path:
    """Create a temporary package directory with a sample UDF."""
    package_dir = tmp_path / "test_package"
    package_dir.mkdir()

    # Create pyproject.toml to make it a valid Python package
    pyproject = package_dir / "pyproject.toml"
    pyproject.write_text(
        dedent(
            """
            [project]
            name = "test_package"
            version = "0.1.0"

            [build-system]
            requires = ["setuptools>=45", "wheel"]
            build-backend = "setuptools.build_meta"
            """
        )
    )

    # Create the Python package directory with the same name
    pkg_source_dir = package_dir / "test_package"
    pkg_source_dir.mkdir()

    # Create package __init__.py
    pkg_init = pkg_source_dir / "__init__.py"
    pkg_init.write_text("")

    # Create the UDF file
    actions_file = pkg_source_dir / "udfs.py"
    actions_file.write_text(basic_udf_content)

    return package_dir


@pytest.mark.anyio
async def test_sync_actions_from_repository_creates_new_actions(
    svc_role: Role,
    local_package_path: Path,
    session: AsyncSession,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Test that sync_actions_from_repository creates new actions in the database."""
    # Setup
    monkeypatch.syspath_prepend(str(local_package_path))
    monkeypatch.setattr(config, "TRACECAT__LOCAL_REPOSITORY_ENABLED", True)
    monkeypatch.setattr(
        config,
        "TRACECAT__LOCAL_REPOSITORY_CONTAINER_PATH",
        str(local_package_path),
    )

    # Create repository
    repo_service = RegistryReposService(session, role=svc_role)
    db_repo = await repo_service.create_repository(
        RegistryRepositoryCreate(origin=DEFAULT_LOCAL_REGISTRY_ORIGIN)
    )

    # Sync actions (use_subprocess=False for testing with monkeypatched config)
    actions_service = RegistryActionsService(session, role=svc_role)
    await actions_service.sync_actions_from_repository(
        db_repo, pull_remote=False, use_subprocess=False
    )

    # Verify action was created
    result = await session.execute(
        select(RegistryAction).where(
            RegistryAction.namespace == "test",
            RegistryAction.name == "add_numbers",
        )
    )
    action = result.scalar_one_or_none()

    assert action is not None
    assert action.namespace == "test"
    assert action.name == "add_numbers"
    assert action.default_title == "Add two numbers"
    assert action.description == "A test action that adds two numbers"
    assert action.repository_id == db_repo.id


@pytest.mark.anyio
async def test_sync_actions_from_repository_updates_existing_actions(
    svc_role: Role,
    local_package_path: Path,
    session: AsyncSession,
    monkeypatch: pytest.MonkeyPatch,
    updated_udf_content: str,
) -> None:
    """Test that sync_actions_from_repository updates existing actions."""
    # Setup
    monkeypatch.syspath_prepend(str(local_package_path))
    monkeypatch.setattr(config, "TRACECAT__LOCAL_REPOSITORY_ENABLED", True)
    monkeypatch.setattr(
        config,
        "TRACECAT__LOCAL_REPOSITORY_CONTAINER_PATH",
        str(local_package_path),
    )

    # Create repository and initial sync
    repo_service = RegistryReposService(session, role=svc_role)
    db_repo = await repo_service.create_repository(
        RegistryRepositoryCreate(origin=DEFAULT_LOCAL_REGISTRY_ORIGIN)
    )

    actions_service = RegistryActionsService(session, role=svc_role)
    await actions_service.sync_actions_from_repository(
        db_repo, pull_remote=False, use_subprocess=False
    )

    # Get initial action
    result = await session.execute(
        select(RegistryAction).where(
            RegistryAction.namespace == "test",
            RegistryAction.name == "add_numbers",
        )
    )
    initial_action = result.scalar_one()
    initial_id = initial_action.id

    # Update the UDF file
    actions_file = local_package_path / "test_package" / "udfs.py"
    actions_file.write_text(updated_udf_content)

    # Sync again (explicitly allow full deletion)
    await actions_service.sync_actions_from_repository(
        db_repo, pull_remote=False, allow_delete_all=True, use_subprocess=False
    )

    # Verify action was updated (same ID, different metadata)
    result = await session.execute(
        select(RegistryAction).where(
            RegistryAction.namespace == "test",
            RegistryAction.name == "add_numbers",
        )
    )
    updated_action = result.scalar_one()

    assert updated_action.id == initial_id  # Same action
    assert updated_action.default_title == "Add three numbers"
    assert updated_action.description == "A test action that adds three numbers"


@pytest.mark.anyio
async def test_sync_actions_from_repository_deletes_removed_actions(
    svc_role: Role,
    local_package_path: Path,
    session: AsyncSession,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Test that sync_actions_from_repository deletes actions removed from the repo."""
    # Setup
    monkeypatch.syspath_prepend(str(local_package_path))
    monkeypatch.setattr(config, "TRACECAT__LOCAL_REPOSITORY_ENABLED", True)
    monkeypatch.setattr(
        config,
        "TRACECAT__LOCAL_REPOSITORY_CONTAINER_PATH",
        str(local_package_path),
    )

    # Create repository and initial sync
    repo_service = RegistryReposService(session, role=svc_role)
    db_repo = await repo_service.create_repository(
        RegistryRepositoryCreate(origin=DEFAULT_LOCAL_REGISTRY_ORIGIN)
    )

    actions_service = RegistryActionsService(session, role=svc_role)
    await actions_service.sync_actions_from_repository(
        db_repo, pull_remote=False, use_subprocess=False
    )

    # Verify action exists
    result = await session.execute(
        select(RegistryAction).where(
            RegistryAction.namespace == "test",
            RegistryAction.name == "add_numbers",
        )
    )
    assert result.scalar_one_or_none() is not None

    # Remove the action from the repository
    actions_file = local_package_path / "test_package" / "udfs.py"
    actions_file.write_text("")  # Empty file, no actions

    # Clear Python's import cache to ensure fresh module load
    import importlib
    import sys

    if "test_package" in sys.modules:
        # Remove the module and all submodules
        modules_to_remove = [
            name for name in sys.modules if name.startswith("test_package")
        ]
        for name in modules_to_remove:
            del sys.modules[name]
    importlib.invalidate_caches()

    # Refresh the session to get latest db_repo with updated actions relationship
    await session.refresh(db_repo)

    # Sync again (explicitly allow removal of all actions)
    await actions_service.sync_actions_from_repository(
        db_repo, pull_remote=False, allow_delete_all=True, use_subprocess=False
    )

    # Verify action was deleted
    result = await session.execute(
        select(RegistryAction).where(
            RegistryAction.namespace == "test",
            RegistryAction.name == "add_numbers",
        )
    )
    assert result.scalar_one_or_none() is None


@pytest.mark.anyio
async def test_sync_actions_from_repository_empty_snapshot_rejected(
    svc_role: Role,
    local_package_path: Path,
    session: AsyncSession,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Ensure we refuse to wipe actions when snapshot is empty."""

    monkeypatch.syspath_prepend(str(local_package_path))
    monkeypatch.setattr(config, "TRACECAT__LOCAL_REPOSITORY_ENABLED", True)
    monkeypatch.setattr(
        config,
        "TRACECAT__LOCAL_REPOSITORY_CONTAINER_PATH",
        str(local_package_path),
    )

    repo_service = RegistryReposService(session, role=svc_role)
    db_repo = await repo_service.create_repository(
        RegistryRepositoryCreate(origin=DEFAULT_LOCAL_REGISTRY_ORIGIN)
    )

    actions_service = RegistryActionsService(session, role=svc_role)

    # Seed with one action
    await actions_service.sync_actions_from_repository(
        db_repo, pull_remote=False, use_subprocess=False
    )

    # Blank out the repo so the snapshot is empty
    actions_file = local_package_path / "test_package" / "udfs.py"
    actions_file.write_text("")

    import importlib
    import sys

    if "test_package" in sys.modules:
        modules_to_remove = [
            name for name in sys.modules if name.startswith("test_package")
        ]
        for name in modules_to_remove:
            del sys.modules[name]
    importlib.invalidate_caches()

    await session.refresh(db_repo)

    with pytest.raises(RegistryError, match="produced no actions"):
        await actions_service.sync_actions_from_repository(
            db_repo, pull_remote=False, use_subprocess=False
        )

    # Original action should remain
    result = await session.execute(
        select(RegistryAction).where(
            RegistryAction.namespace == "test",
            RegistryAction.name == "add_numbers",
        )
    )

    assert result.scalar_one_or_none() is not None


@pytest.mark.anyio
async def test_sync_actions_from_repository_consistency_check(
    svc_role: Role,
    local_package_path: Path,
    session: AsyncSession,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Test that multiple syncs maintain consistency of the RegistryActions table.

    This test verifies that:
    1. Re-syncing the same repository produces the same state
    2. Action counts remain consistent
    3. No duplicate actions are created
    """
    # Setup
    monkeypatch.syspath_prepend(str(local_package_path))
    monkeypatch.setattr(config, "TRACECAT__LOCAL_REPOSITORY_ENABLED", True)
    monkeypatch.setattr(
        config,
        "TRACECAT__LOCAL_REPOSITORY_CONTAINER_PATH",
        str(local_package_path),
    )

    # Create repository
    repo_service = RegistryReposService(session, role=svc_role)
    db_repo = await repo_service.create_repository(
        RegistryRepositoryCreate(origin=DEFAULT_LOCAL_REGISTRY_ORIGIN)
    )

    actions_service = RegistryActionsService(session, role=svc_role)

    # First sync
    await actions_service.sync_actions_from_repository(
        db_repo, pull_remote=False, use_subprocess=False
    )

    # Get state after first sync
    result = await session.execute(
        select(RegistryAction).where(RegistryAction.repository_id == db_repo.id)
    )
    actions_after_first_sync = result.scalars().all()
    first_sync_count = len(actions_after_first_sync)
    first_sync_action_names = {
        f"{action.namespace}.{action.name}" for action in actions_after_first_sync
    }

    # Second sync (should be idempotent)
    await actions_service.sync_actions_from_repository(
        db_repo, pull_remote=False, use_subprocess=False
    )

    # Get state after second sync
    result = await session.execute(
        select(RegistryAction).where(RegistryAction.repository_id == db_repo.id)
    )
    actions_after_second_sync = result.scalars().all()
    second_sync_count = len(actions_after_second_sync)
    second_sync_action_names = {
        f"{action.namespace}.{action.name}" for action in actions_after_second_sync
    }

    # Verify consistency
    assert first_sync_count == second_sync_count
    assert first_sync_action_names == second_sync_action_names
    assert first_sync_count > 0  # Ensure we actually synced something

    # Third sync (verify still consistent)
    await actions_service.sync_actions_from_repository(
        db_repo, pull_remote=False, use_subprocess=False
    )

    result = await session.execute(
        select(RegistryAction).where(RegistryAction.repository_id == db_repo.id)
    )
    actions_after_third_sync = result.scalars().all()
    third_sync_count = len(actions_after_third_sync)

    assert first_sync_count == third_sync_count


@pytest.mark.anyio
async def test_sync_actions_from_repository_with_malformed_function(
    svc_role: Role,
    tmp_path: Path,
    session: AsyncSession,
    monkeypatch: pytest.MonkeyPatch,
    malformed_udf_content: str,
) -> None:
    """Test that sync fails when a malformed function cannot be imported.

    This test demonstrates that the sync operation does NOT run inside a transaction,
    so if an import error occurs, any previously synced actions will remain in the
    database, leading to an inconsistent state.
    """
    # Create a package with a malformed UDF
    package_dir = tmp_path / "test_package_malformed"
    package_dir.mkdir()

    # Create pyproject.toml
    pyproject = package_dir / "pyproject.toml"
    pyproject.write_text(
        dedent(
            """
            [project]
            name = "test_package_malformed"
            version = "0.1.0"

            [build-system]
            requires = ["setuptools>=45", "wheel"]
            build-backend = "setuptools.build_meta"
            """
        )
    )

    pkg_source_dir = package_dir / "test_package_malformed"
    pkg_source_dir.mkdir()

    init_file = pkg_source_dir / "__init__.py"
    init_file.write_text("")

    actions_file = pkg_source_dir / "udfs.py"
    actions_file.write_text(malformed_udf_content)

    # Setup
    monkeypatch.syspath_prepend(str(package_dir))
    monkeypatch.setattr(config, "TRACECAT__LOCAL_REPOSITORY_ENABLED", True)
    monkeypatch.setattr(
        config,
        "TRACECAT__LOCAL_REPOSITORY_CONTAINER_PATH",
        str(package_dir),
    )

    # Create repository
    repo_service = RegistryReposService(session, role=svc_role)
    db_repo = await repo_service.create_repository(
        RegistryRepositoryCreate(origin=DEFAULT_LOCAL_REGISTRY_ORIGIN)
    )

    actions_service = RegistryActionsService(session, role=svc_role)

    # Attempt to sync - this should fail due to import error
    with pytest.raises(ImportError, match="nonexistent_module"):
        await actions_service.sync_actions_from_repository(
            db_repo, pull_remote=False, use_subprocess=False
        )

    # The test expects that if there were any partially synced actions,
    # they would remain in the database because there's no transaction rollback.
    # In this specific case, the import fails before any actions are registered,
    # so there should be no actions in the database.
    result = await session.execute(
        select(RegistryAction).where(RegistryAction.repository_id == db_repo.id)
    )
    actions = result.scalars().all()

    # This assertion may vary depending on when the import error occurs
    # If the error occurs during module import, no actions will be created
    # If the error occurs after some actions are created, those will remain
    assert len(actions) == 0


@pytest.mark.anyio
async def test_sync_actions_from_repository_mixed_valid_and_malformed(
    svc_role: Role,
    tmp_path: Path,
    session: AsyncSession,
    monkeypatch: pytest.MonkeyPatch,
    basic_udf_content: str,
) -> None:
    """Test sync with multiple UDFs where one is malformed.

    This test demonstrates the lack of transactional safety: if we have multiple
    UDF files and one fails to import, previously imported actions may still be
    committed to the database, resulting in an inconsistent state.
    """
    # Create a package with multiple UDF files
    package_dir = tmp_path / "test_package_mixed"
    package_dir.mkdir()

    # Create pyproject.toml
    pyproject = package_dir / "pyproject.toml"
    pyproject.write_text(
        dedent(
            """
            [project]
            name = "test_package_mixed"
            version = "0.1.0"

            [build-system]
            requires = ["setuptools>=45", "wheel"]
            build-backend = "setuptools.build_meta"
            """
        )
    )

    pkg_source_dir = package_dir / "test_package_mixed"
    pkg_source_dir.mkdir()

    init_file = pkg_source_dir / "__init__.py"
    init_file.write_text("")

    # First valid UDF file
    valid_file = pkg_source_dir / "valid_udfs.py"
    valid_file.write_text(basic_udf_content)

    # Second file with malformed UDF
    malformed_file = pkg_source_dir / "malformed_udfs.py"
    malformed_file.write_text(
        dedent(
            """
            from tracecat_registry import registry

            # This will fail at import time
            from nonexistent_package import something

            @registry.register(
                default_title="Bad action",
                namespace="test",
                description="This will fail",
            )
            def bad_action(x: int) -> int:
                return x
            """
        )
    )

    # Setup
    monkeypatch.syspath_prepend(str(package_dir))
    monkeypatch.setattr(config, "TRACECAT__LOCAL_REPOSITORY_ENABLED", True)
    monkeypatch.setattr(
        config,
        "TRACECAT__LOCAL_REPOSITORY_CONTAINER_PATH",
        str(package_dir),
    )

    # Create repository
    repo_service = RegistryReposService(session, role=svc_role)
    db_repo = await repo_service.create_repository(
        RegistryRepositoryCreate(origin=DEFAULT_LOCAL_REGISTRY_ORIGIN)
    )

    actions_service = RegistryActionsService(session, role=svc_role)

    # Attempt to sync - this should fail
    with pytest.raises(ImportError):
        await actions_service.sync_actions_from_repository(
            db_repo, pull_remote=False, use_subprocess=False
        )

    # Check if any actions were created before the failure
    # This demonstrates the lack of transaction: if the valid file was processed
    # before the malformed one, those actions might be in the database
    result = await session.execute(
        select(RegistryAction).where(RegistryAction.repository_id == db_repo.id)
    )
    actions = result.scalars().all()

    # The exact behavior depends on import order and whether the error occurs
    # before or after some actions are registered
    # This test documents the current behavior: no transaction rollback means
    # we could have partial state
    assert len(actions) == 0


@pytest.mark.anyio
async def test_get_aggregated_secrets_merges_skips_oauth_and_sorts(
    svc_role: Role,
    session: AsyncSession,
) -> None:
    """Test secret aggregation across registry manifests.

    The get_aggregated_secrets method queries manifests from RegistryVersion
    tables, not RegistryAction tables directly.
    """
    from tracecat.db.models import RegistryRepository, RegistryVersion

    # Create a repository for the current organization
    repo = RegistryRepository(
        organization_id=config.TRACECAT__DEFAULT_ORG_ID,
        origin="test-secrets",
    )
    session.add(repo)
    await session.flush()

    # Create manifest with actions that have secrets
    manifest = {
        "schema_version": "1.0",
        "actions": {
            "tools.alpha.action_one": {
                "namespace": "tools.alpha",
                "name": "action_one",
                "action_type": "template",
                "description": "Action one",
                "interface": {"expects": {}, "returns": None},
                "implementation": {
                    "type": "udf",
                    "url": "test",
                    "module": "test",
                    "name": "action_one",
                },
                "secrets": [
                    {
                        "name": "alpha",
                        "keys": ["KEY1"],
                        "optional_keys": ["KEY2", "KEY1"],
                    },
                    {
                        "type": "oauth",
                        "provider_id": "github",
                        "grant_type": "authorization_code",
                    },
                ],
            },
            "tools.beta.action_two": {
                "namespace": "tools.beta",
                "name": "action_two",
                "action_type": "template",
                "description": "Action two",
                "interface": {"expects": {}, "returns": None},
                "implementation": {
                    "type": "udf",
                    "url": "test",
                    "module": "test",
                    "name": "action_two",
                },
                "secrets": [
                    {
                        "name": "alpha",
                        "keys": ["KEY3"],
                        "optional_keys": ["KEY4"],
                        "optional": True,
                    },
                    {"name": "beta", "keys": ["B1"]},
                ],
            },
            "tools.delta.action_four": {
                "namespace": "tools.delta",
                "name": "action_four",
                "action_type": "template",
                "description": "Action four",
                "interface": {"expects": {}, "returns": None},
                "implementation": {
                    "type": "udf",
                    "url": "test",
                    "module": "test",
                    "name": "action_four",
                },
                # No secrets
            },
        },
    }

    # Create version with manifest
    version = RegistryVersion(
        organization_id=config.TRACECAT__DEFAULT_ORG_ID,
        repository_id=repo.id,
        version="test-version",
        manifest=manifest,
        tarball_uri="s3://test/test.tar.gz",
    )
    session.add(version)
    await session.flush()

    # Set current version on repository
    repo.current_version_id = version.id
    await session.commit()

    service = RegistryActionsService(session, role=svc_role)
    definitions = await service.get_aggregated_secrets()

    assert [definition.name for definition in definitions] == ["alpha", "beta"]

    alpha = definitions[0]
    assert alpha.keys == ["KEY1", "KEY3"]
    assert alpha.optional_keys == ["KEY2", "KEY4"]
    assert alpha.optional is True
    assert alpha.actions == ["tools.alpha.action_one", "tools.beta.action_two"]
    assert alpha.action_count == 2

    beta = definitions[1]
    assert beta.keys == ["B1"]
    assert beta.optional_keys is None
    assert beta.optional is False
    assert beta.actions == ["tools.beta.action_two"]
    assert beta.action_count == 1
