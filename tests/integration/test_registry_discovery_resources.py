"""Exercise registry discovery resource headroom in a real NsJail process.

Run this test directly so the integration parent conftest does not disable
NsJail::

    uv run pytest --noconftest tests/integration/test_registry_discovery_resources.py -m integration -s

The test is skipped unless Linux and the configured NsJail sandbox rootfs are
available.
"""

from __future__ import annotations

import sys
from pathlib import Path
from typing import Literal
from uuid import uuid4

import pytest

from tracecat import config
from tracecat.registry.sync.sandbox import RegistrySyncSandbox


def _real_nsjail_available() -> bool:
    """Return whether this host can run the real registry discovery jail."""
    return (
        sys.platform == "linux"
        and Path(config.TRACECAT__SANDBOX_NSJAIL_PATH).is_file()
        and Path(config.TRACECAT__SANDBOX_ROOTFS_PATH).is_dir()
    )


pytestmark = [
    pytest.mark.integration,
    pytest.mark.skipif(
        not _real_nsjail_available(),
        reason="real registry discovery NsJail requires Linux, NsJail, and its rootfs",
    ),
]


@pytest.fixture
def anyio_backend() -> Literal["asyncio"]:
    """Use the asyncio backend for direct no-conftest invocation."""
    return "asyncio"


@pytest.mark.anyio
async def test_registry_discovery_survives_native_import_reservations(
    tmp_path: Path,
) -> None:
    """Discover an action after native imports reserve allocator address space."""
    site_packages = tmp_path / "sandbox-install" / "cache" / "site-packages"
    package_path = site_packages / "synthetic_registry"
    package_path.mkdir(parents=True)
    (package_path / "__init__.py").write_text(
        """from __future__ import annotations

import os
import time

os.environ["OPENBLAS_NUM_THREADS"] = "12"
import numpy
import duckdb

duckdb.sql("SET threads=20")
time.sleep(1)
import databricks.sdk

from tracecat_registry import registry


@registry.register(
    description="Synthetic action",
    namespace="tools.synthetic",
)
def synthetic_action() -> str:
    return "synthetic"
""",
        encoding="utf-8",
    )
    # Repository discovery scans importable modules below the package root;
    # re-export the package-level registration through one such module.
    (package_path / "actions.py").write_text(
        "from synthetic_registry import synthetic_action\n",
        encoding="utf-8",
    )

    result = await RegistrySyncSandbox().discover_actions(
        site_packages=site_packages,
        origin="synthetic://registry",
        package_name="synthetic_registry",
        repository_id=uuid4(),
        commit_sha=None,
        validate=False,
        organization_id=None,
        timeout_seconds=45,
    )

    assert len(result.actions) == 1
    action = result.actions[0]
    assert action.name == "synthetic_action"
    assert action.namespace == "tools.synthetic"
    assert action.description == "Synthetic action"
