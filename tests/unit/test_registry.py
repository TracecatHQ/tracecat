"""Tests for registry UDFs, template actions, and git repository sync."""

import os
import sys
import tempfile
import textwrap
from datetime import datetime
from importlib.machinery import ModuleSpec
from types import ModuleType
from uuid import UUID

import pytest

from tracecat.exceptions import RegistryValidationError
from tracecat.git.utils import GitUrl, parse_git_url
from tracecat.registry.actions.service import RegistryActionsService
from tracecat.registry.repository import Repository


@pytest.mark.anyio
async def test_list_registry_actions(test_role):
    """Test that list_actions_from_index returns actions."""
    async with RegistryActionsService.with_session(test_role) as service:
        entries = await service.list_actions_from_index()
        assert len(entries) > 0
        # Verify each entry is a tuple of (IndexEntry, origin)
        for entry, origin in entries:
            assert entry.namespace
            assert entry.name
            assert isinstance(origin, str)


@pytest.fixture
def mock_package(tmp_path):
    """Pytest fixture that creates a mock package with files and cleans up after the test."""
    import sys
    from importlib.machinery import ModuleSpec
    from types import ModuleType

    # Create a new module
    test_module = ModuleType("test_module")

    # Create a module spec for the test module
    module_spec = ModuleSpec("test_module", None)
    test_module.__spec__ = module_spec
    # Set __path__ to the temporary directory
    test_module.__path__ = [str(tmp_path)]

    try:
        # Add the module to sys.modules
        sys.modules["test_module"] = test_module
        # Create a file for the sync function
        with open(os.path.join(tmp_path, "sync_function.py"), "w") as f:
            f.write(
                textwrap.dedent(
                    """
                from tracecat_registry import registry

                @registry.register(
                    description="This is a test function",
                    namespace="test",
                    doc_url="https://example.com/docs",
                    author="Tracecat",
                )
                def test_function(num: int) -> int:
                    return num
            """
                )
            )

        # Create a file for the async function
        with open(os.path.join(tmp_path, "async_function.py"), "w") as f:
            f.write(
                textwrap.dedent(
                    """
                from tracecat_registry import registry

                @registry.register(
                    description="This is an async test function",
                    namespace="test",
                    doc_url="https://example.com/docs",
                    author="Tracecat",
                )
                async def async_test_function(num: int) -> int:
                    return num
            """
                )
            )

        # Create a file for the deprecated function
        with open(os.path.join(tmp_path, "deprecated_function.py"), "w") as f:
            f.write(
                textwrap.dedent(
                    """
                from tracecat_registry import registry

                @registry.register(
                    description="This is a deprecated function",
                    namespace="test",
                    deprecated="This function is deprecated",
                )
                def deprecated_function(num: int) -> int:
                    return num
            """
                )
            )

        yield test_module

    finally:
        # Clean up
        del sys.modules["test_module"]


def test_udf_can_be_registered(mock_package):
    """Test that a UDF can be registered."""
    repo = Repository()
    repo._register_udfs_from_package(mock_package)
    assert repo.get("test.test_function") is not None


def test_udf_validate_args(mock_package):
    """This tests the UDF.validate_args method, which shouldn't raise any exceptions
    when given a templated expression.
    """
    # Register UDFs from the mock package
    repo = Repository()
    repo._register_udfs_from_package(mock_package)

    # Get the registered UDF
    udf = repo.get("test.test_function")

    # Check descriptors
    assert udf.description == "This is a test function"
    assert udf.namespace == "test"
    assert udf.doc_url == "https://example.com/docs"
    assert udf.author == "Tracecat"

    # Test the UDF
    udf.validate_args(args={"num": "${{ path.to.number }}"})
    udf.validate_args(args={"num": 1})
    with pytest.raises(RegistryValidationError):
        udf.validate_args(args={"num": "not a number"})


def test_deprecated_function_can_be_registered(mock_package):
    """Test that a deprecated function can be registered."""
    repo = Repository()
    repo._register_udfs_from_package(mock_package)

    udf = repo.get("test.deprecated_function")
    assert udf is not None

    # Check descriptors
    assert udf.deprecated == "This function is deprecated"


def test_registry_function_can_be_called(mock_package):
    """We need to test that the ordering of the workflow tasks is correct."""
    repo = Repository()
    assert len(repo) == 0

    repo._register_udfs_from_package(mock_package)
    udf = repo.get("test.test_function")
    for i in range(10):
        assert udf.fn(num=i) == i


@pytest.mark.anyio
async def test_registry_async_function_can_be_called(mock_package):
    repo = Repository()
    assert len(repo) == 0

    repo._register_udfs_from_package(mock_package)
    udf = repo.get("test.async_test_function")
    for i in range(10):
        assert await udf.fn(num=i) == i


def test_validate_args_mode_parameter(tmp_path):
    """Test that validate_args mode parameter works correctly with default and explicit values."""

    # Create a new test module with datetime/UUID UDFs
    test_module = ModuleType("test_mode_module")
    module_spec = ModuleSpec("test_mode_module", None)
    test_module.__spec__ = module_spec
    test_module.__path__ = [str(tmp_path)]

    try:
        sys.modules["test_mode_module"] = test_module

        # Create a file with UDF that accepts datetime
        with tempfile.NamedTemporaryFile(
            mode="w", suffix=".py", dir=tmp_path, delete=False
        ) as f:
            f.write(
                textwrap.dedent(
                    """
                from datetime import datetime
                from uuid import UUID
                from tracecat_registry import registry

                @registry.register(
                    description="Test function with datetime",
                    namespace="test",
                    doc_url="https://example.com/docs",
                    author="Tracecat",
                )
                def datetime_test(dt: datetime, uid: UUID) -> dict:
                    return {"dt": dt, "uid": uid}
            """
                )
            )

        # Register the UDF
        repo = Repository()
        repo._register_udfs_from_package(test_module)
        udf = repo.get("test.datetime_test")

        # Test data
        test_datetime = datetime(2024, 1, 15, 10, 30, 0)
        test_uuid = UUID("a1b2c3d4-e5f6-7890-abcd-ef1234567890")

        # TEST 1: Default mode should be "json"
        result_default = udf.validate_args(args={"dt": test_datetime, "uid": test_uuid})
        assert isinstance(result_default["dt"], str), (
            "Default mode should serialize datetime to string"
        )
        assert isinstance(result_default["uid"], str), (
            "Default mode should serialize UUID to string"
        )
        assert result_default["dt"] == "2024-01-15T10:30:00"
        assert result_default["uid"] == "a1b2c3d4-e5f6-7890-abcd-ef1234567890"

        # TEST 2: Explicit mode="json"
        result_json = udf.validate_args(
            args={"dt": test_datetime, "uid": test_uuid}, mode="json"
        )
        assert isinstance(result_json["dt"], str), (
            "JSON mode should serialize datetime to string"
        )
        assert isinstance(result_json["uid"], str), (
            "JSON mode should serialize UUID to string"
        )
        assert result_json["dt"] == "2024-01-15T10:30:00"
        assert result_json["uid"] == "a1b2c3d4-e5f6-7890-abcd-ef1234567890"

        # TEST 3: Explicit mode="python"
        result_python = udf.validate_args(
            args={"dt": test_datetime, "uid": test_uuid}, mode="python"
        )
        assert isinstance(result_python["dt"], datetime), (
            "Python mode should preserve datetime type"
        )
        assert isinstance(result_python["uid"], UUID), (
            "Python mode should preserve UUID type"
        )
        assert result_python["dt"] == test_datetime
        assert result_python["uid"] == test_uuid

        # TEST 4: Template expressions work in both modes
        # Should not raise RegistryValidationError
        udf.validate_args(
            args={"dt": "${{ INPUTS.timestamp }}", "uid": "${{ INPUTS.id }}"}
        )
        udf.validate_args(
            args={"dt": "${{ INPUTS.timestamp }}", "uid": "${{ INPUTS.id }}"},
            mode="json",
        )
        udf.validate_args(
            args={"dt": "${{ INPUTS.timestamp }}", "uid": "${{ INPUTS.id }}"},
            mode="python",
        )

    finally:
        # Clean up
        del sys.modules["test_mode_module"]


@pytest.mark.parametrize(
    "url, expected",
    [
        # GitHub (no branch)
        (
            "git+ssh://git@github.com/org/repo",
            GitUrl(
                host="github.com",
                org="org",
                repo="repo",
                ref=None,
            ),
        ),
        # GitHub (with branch/sha)
        (
            "git+ssh://git@github.com/org/repo@branchOrSHAOrTag",
            GitUrl(
                host="github.com",
                org="org",
                repo="repo",
                ref="branchOrSHAOrTag",
            ),
        ),
        # GitLab
        (
            "git+ssh://git@gitlab.com/org/repo",
            GitUrl(
                host="gitlab.com",
                org="org",
                repo="repo",
                ref=None,
            ),
        ),
        # GitLab (with branch)
        (
            "git+ssh://git@gitlab.com/org/repo@branch",
            GitUrl(
                host="gitlab.com",
                org="org",
                repo="repo",
                ref="branch",
            ),
        ),
        # Private GitLab
        (
            "git+ssh://git@internal.tracecat/org/repo",
            GitUrl(
                host="internal.tracecat",
                org="org",
                repo="repo",
                ref=None,
            ),
        ),
        # # Private GitLab nested in a subdirectory
        # (
        #     "git+ssh://git@internal.tracecat/org/group/repo",
        #     GitUrl(
        #         host="internal.tracecat",
        #         org="org/group",
        #         repo="repo",
        #         ref=None,
        #     ),
        # ),
    ],
)
def test_parse_git_url(url: str, expected: GitUrl):
    assert parse_git_url(url) == expected


@pytest.mark.parametrize(
    "url",
    [
        pytest.param(
            "git+ssh://git@tracecat.com/tracecat-dev/tracecat-registry.git@v1.0.0",
            id="Invalid host domain tracecat.com",
        ),
        pytest.param(
            "git+ssh://git@git.com/tracecat-dev/tracecat-registry.git@v1.0.0",
            id="Invalid host domain git.com",
        ),
        pytest.param(
            "https://github.com/org/repo",
            id="Invalid URL scheme - must be git+ssh",
        ),
        pytest.param(
            "git+ssh://git@github.com/org",
            id="Missing repository name",
        ),
        pytest.param(
            "git+ssh://git@github.com/org/repo@branch/extra",
            id="Invalid branch format with extra path component",
        ),
    ],
)
def test_parse_git_url_invalid(url: str):
    allowed_domains = {"github.com", "gitlab.com"}
    with pytest.raises(ValueError):
        parse_git_url(url, allowed_domains=allowed_domains)


def test_iter_valid_files(tmp_path):
    """Test iter_valid_files function with various file structures and exclusion rules."""
    from tracecat.registry.repository import iter_valid_files

    # Create a test directory structure
    # Valid Python files
    (tmp_path / "module1.py").touch()
    (tmp_path / "module2.py").touch()

    # Should be excluded - __init__ and __main__
    (tmp_path / "__init__.py").touch()
    (tmp_path / "__main__.py").touch()

    # Hidden/private files - should be excluded
    (tmp_path / ".hidden.py").touch()
    (tmp_path / "_private.py").touch()

    # Subdirectory with valid files
    subdir = tmp_path / "subpackage"
    subdir.mkdir()
    (subdir / "sub_module.py").touch()
    (subdir / "__init__.py").touch()

    # Virtual environment directory - should be excluded entirely
    venv_dir = tmp_path / ".venv"
    venv_dir.mkdir()
    (venv_dir / "bin").mkdir()
    (venv_dir / "bin" / "activate.py").touch()
    (venv_dir / "lib").mkdir()
    (venv_dir / "lib" / "site-packages").mkdir()
    (venv_dir / "lib" / "site-packages" / "package.py").touch()

    # Another common venv name - should be excluded
    venv_dir2 = tmp_path / "venv"
    venv_dir2.mkdir()
    (venv_dir2 / "some_module.py").touch()

    # Build directories - should be excluded
    build_dir = tmp_path / "build"
    build_dir.mkdir()
    (build_dir / "compiled.py").touch()

    dist_dir = tmp_path / "dist"
    dist_dir.mkdir()
    (dist_dir / "package.py").touch()

    # Cache directories - should be excluded
    cache_dir = tmp_path / "__pycache__"
    cache_dir.mkdir()
    (cache_dir / "module.cpython-311.pyc").touch()

    mypy_cache = tmp_path / ".mypy_cache"
    mypy_cache.mkdir()
    (mypy_cache / "cache.py").touch()

    # Non-Python files - should be excluded when looking for .py
    (tmp_path / "readme.md").touch()
    (tmp_path / "config.yaml").touch()
    (tmp_path / "data.json").touch()

    # YAML files for testing yaml extension filter
    (tmp_path / "template1.yml").touch()
    (tmp_path / "template2.yaml").touch()
    (subdir / "sub_template.yml").touch()

    # Non-identifier directory names - should be excluded
    non_ident_dir = tmp_path / "123-invalid"
    non_ident_dir.mkdir()
    (non_ident_dir / "module.py").touch()

    # Test 1: Default behavior - Python files with explicit exclusions
    py_files = list(
        iter_valid_files(
            tmp_path,
            file_extensions=(".py",),
            exclude_filenames=(
                "__init__",
                "__main__",
            ),  # Explicit exclusions (though redundant due to _ prefix)
        )
    )

    # Convert to relative paths for easier assertions
    py_file_names = [f.relative_to(tmp_path).as_posix() for f in py_files]

    # Should include only valid Python files
    assert "module1.py" in py_file_names
    assert "module2.py" in py_file_names
    assert "subpackage/sub_module.py" in py_file_names

    # These are excluded by BOTH the exclude_filenames parameter AND the _ prefix check
    assert "__init__.py" not in py_file_names
    assert "__main__.py" not in py_file_names
    assert "subpackage/__init__.py" not in py_file_names

    # These are excluded by the hidden/private file check
    assert ".hidden.py" not in py_file_names
    assert "_private.py" not in py_file_names

    # Should not include anything from excluded directories
    assert not any(".venv" in f for f in py_file_names)
    assert not any("venv" in f for f in py_file_names)
    assert not any("build" in f for f in py_file_names)
    assert not any("dist" in f for f in py_file_names)
    assert not any("__pycache__" in f for f in py_file_names)
    assert not any(".mypy_cache" in f for f in py_file_names)
    assert not any("123-invalid" in f for f in py_file_names)

    # Test 2: YAML files only
    yaml_files = list(
        iter_valid_files(
            tmp_path,
            file_extensions=(".yml", ".yaml"),
            exclude_filenames=(),  # No exclusions for yaml
        )
    )

    yaml_file_names = [f.relative_to(tmp_path).as_posix() for f in yaml_files]

    assert "template1.yml" in yaml_file_names
    assert "template2.yaml" in yaml_file_names
    assert "subpackage/sub_template.yml" in yaml_file_names

    # Should not include Python files
    assert "module1.py" not in yaml_file_names
    assert "module2.py" not in yaml_file_names

    # Test 3: Custom exclusions
    custom_files = list(
        iter_valid_files(
            tmp_path,
            file_extensions=(".py",),
            exclude_filenames=("module1",),  # Exclude module1
            exclude_dirnames={"subpackage"},  # Exclude subpackage directory
        )
    )

    custom_file_names = [f.relative_to(tmp_path).as_posix() for f in custom_files]

    assert "module2.py" in custom_file_names
    assert "module1.py" not in custom_file_names  # Excluded by filename
    assert "subpackage/sub_module.py" not in custom_file_names  # Excluded by directory

    # Test 4: No explicit filename exclusions (but hidden/private files still excluded)
    # When exclude_filenames is None (default), no specific filename exclusions are applied
    # However, files starting with . or _ are ALWAYS excluded as hidden/private
    all_py_files = list(
        iter_valid_files(
            tmp_path,
            file_extensions=(".py",),
            exclude_filenames=None,  # No explicit exclusions
        )
    )

    all_py_file_names = [f.relative_to(tmp_path).as_posix() for f in all_py_files]

    # Regular Python files should be included
    assert "module1.py" in all_py_file_names
    assert "module2.py" in all_py_file_names
    assert "subpackage/sub_module.py" in all_py_file_names

    # Files starting with _ are ALWAYS excluded as private files
    # This includes __init__.py and __main__.py (they start with _)
    assert "__init__.py" not in all_py_file_names
    assert "__main__.py" not in all_py_file_names
    assert "subpackage/__init__.py" not in all_py_file_names
    assert "_private.py" not in all_py_file_names

    # Hidden files (starting with .) are ALWAYS excluded
    assert ".hidden.py" not in all_py_file_names

    # Virtual env directories still excluded
    assert not any(".venv" in f for f in all_py_file_names)

    # The result should be the same as Test 1 since __init__ and __main__
    # are excluded by the private file check, not by exclude_filenames
    assert set(all_py_file_names) == {
        "module1.py",
        "module2.py",
        "subpackage/sub_module.py",
    }
