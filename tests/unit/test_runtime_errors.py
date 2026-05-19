from __future__ import annotations

from tracecat.runtime.errors import is_known_infra_exception


def test_is_known_infra_exception_checks_implicit_context() -> None:
    try:
        try:
            raise OSError("disk full")
        except OSError:
            raise RuntimeError("wrapped")  # noqa: B904 - intentionally implicit
    except RuntimeError as error:
        assert is_known_infra_exception(error) is True


def test_is_known_infra_exception_respects_suppressed_context() -> None:
    try:
        try:
            raise OSError("disk full")
        except OSError:
            raise RuntimeError("wrapped") from None
    except RuntimeError as error:
        assert is_known_infra_exception(error) is False
