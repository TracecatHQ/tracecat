from tests.conftest import (
    _should_select_fixture_platform_current,
)
from tests.database import TEST_DB_CONFIG


def test_fixture_platform_current_is_selected_in_per_test_db() -> None:
    assert _should_select_fixture_platform_current(
        TEST_DB_CONFIG.test_url_sync,
        current_version=None,
        fixture_version="test-version",
    )


def test_fixture_platform_current_is_selected_for_empty_default_db() -> None:
    assert _should_select_fixture_platform_current(
        "postgresql+psycopg://postgres:postgres@localhost:5432/postgres",
        current_version=None,
        fixture_version="test-version",
    )


def test_fixture_platform_current_is_reselected_when_already_current() -> None:
    assert _should_select_fixture_platform_current(
        "postgresql+psycopg://postgres:postgres@localhost:5432/postgres",
        current_version="test-version",
        fixture_version="test-version",
    )


def test_fixture_platform_current_preserves_live_default_current() -> None:
    assert not _should_select_fixture_platform_current(
        "postgresql+psycopg://postgres:postgres@localhost:5432/postgres",
        current_version="1.0.0-beta.40",
        fixture_version="test-version",
    )
