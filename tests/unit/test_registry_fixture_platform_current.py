from tests.conftest import (
    _is_live_platform_current,
    _should_select_fixture_platform_current,
    _should_wait_for_live_platform_current,
)
from tests.database import TEST_DB_CONFIG


def test_fixture_platform_current_is_selected_in_per_test_db() -> None:
    assert _should_select_fixture_platform_current(
        TEST_DB_CONFIG.test_url_sync,
        current_version=None,
        fixture_version="test-version",
    )


def test_fixture_platform_current_is_not_selected_for_empty_default_db() -> None:
    assert not _should_select_fixture_platform_current(
        "postgresql+psycopg://postgres:postgres@localhost:5432/postgres",
        current_version=None,
        fixture_version="test-version",
        executor_backend="direct",
    )


def test_fixture_platform_current_is_selected_for_test_backend_default_db() -> None:
    assert _should_select_fixture_platform_current(
        "postgresql+psycopg://postgres:postgres@localhost:5432/postgres",
        current_version=None,
        fixture_version="test-version",
        executor_backend="test",
    )


def test_fixture_platform_current_is_reselected_when_already_current() -> None:
    assert _should_select_fixture_platform_current(
        "postgresql+psycopg://postgres:postgres@localhost:5432/postgres",
        current_version="test-version",
        fixture_version="test-version",
        executor_backend="direct",
    )


def test_fixture_platform_current_preserves_live_default_current() -> None:
    assert not _should_select_fixture_platform_current(
        "postgresql+psycopg://postgres:postgres@localhost:5432/postgres",
        current_version="1.0.0-beta.40",
        fixture_version="test-version",
        executor_backend="test",
    )


def test_fixture_waits_for_empty_default_db_current_with_real_executor() -> None:
    assert _should_wait_for_live_platform_current(
        "postgresql+psycopg://postgres:postgres@localhost:5432/postgres",
        current_version=None,
        executor_backend="direct",
    )


def test_fixture_does_not_wait_for_test_backend_default_db_current() -> None:
    assert not _should_wait_for_live_platform_current(
        "postgresql+psycopg://postgres:postgres@localhost:5432/postgres",
        current_version=None,
        executor_backend="test",
    )


def test_fixture_does_not_wait_for_per_test_db_current() -> None:
    assert not _should_wait_for_live_platform_current(
        TEST_DB_CONFIG.test_url_sync,
        current_version=None,
        executor_backend="direct",
    )


def test_live_platform_current_requires_expected_real_tarball() -> None:
    assert _is_live_platform_current(
        version="1.0.0-beta.40",
        tarball_uri="s3://tracecat-registry/platform/tarball.tar.gz",
        expected_version="1.0.0-beta.40",
        fixture_version="test-version",
        fixture_tarball_uri="s3://test/test.tar.gz",
    )


def test_live_platform_current_rejects_fixture_tarball() -> None:
    assert not _is_live_platform_current(
        version="test-version",
        tarball_uri="s3://test/test.tar.gz",
        expected_version="1.0.0-beta.40",
        fixture_version="test-version",
        fixture_tarball_uri="s3://test/test.tar.gz",
    )
