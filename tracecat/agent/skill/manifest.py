"""Manifest rules shared by workspace skills and platform library entries."""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass
from pathlib import PurePosixPath

from tracecat import config
from tracecat.exceptions import TracecatValidationError


@dataclass(frozen=True, slots=True)
class SkillFileSizeMetadata:
    """Path and declared byte size used for skill-wide limit checks."""

    path: str | None
    size_bytes: int


@dataclass(frozen=True, slots=True)
class SkillFileLimitViolation:
    """One deterministic skill-tree limit violation."""

    code: str
    message: str
    path: str | None
    actual_field: str
    actual_value: int
    limit_field: str
    limit_value: int

    def exception_detail(self) -> dict[str, str | int]:
        """Return structured API error details for this violation."""

        detail: dict[str, str | int] = {
            "code": self.code,
            self.actual_field: self.actual_value,
            self.limit_field: self.limit_value,
        }
        if self.path is not None:
            detail["path"] = self.path
        return detail


def skill_file_limit_violation(
    files: Sequence[SkillFileSizeMetadata],
) -> SkillFileLimitViolation | None:
    """Return the first deterministic skill-tree limit violation."""

    if len(files) > config.TRACECAT__MAX_SKILL_FILES_COUNT:
        return SkillFileLimitViolation(
            code="skill_file_count_limit_exceeded",
            message="Skill draft contains too many files",
            path=None,
            actual_field="file_count",
            actual_value=len(files),
            limit_field="max_file_count",
            limit_value=config.TRACECAT__MAX_SKILL_FILES_COUNT,
        )

    manifest = next((file for file in files if file.path == "SKILL.md"), None)
    if (
        manifest is not None
        and manifest.size_bytes > config.TRACECAT__MAX_SKILL_MANIFEST_SIZE_BYTES
    ):
        return SkillFileLimitViolation(
            code="skill_manifest_size_limit_exceeded",
            message="Root SKILL.md exceeds the size limit",
            path="SKILL.md",
            actual_field="size_bytes",
            actual_value=manifest.size_bytes,
            limit_field="max_size_bytes",
            limit_value=config.TRACECAT__MAX_SKILL_MANIFEST_SIZE_BYTES,
        )

    oversized_file = max(
        (
            file
            for file in files
            if file.size_bytes > config.TRACECAT__MAX_SKILL_FILE_SIZE_BYTES
        ),
        key=lambda file: (file.size_bytes, file.path or ""),
        default=None,
    )
    if oversized_file is not None:
        return SkillFileLimitViolation(
            code="skill_file_size_limit_exceeded",
            message="Skill file exceeds the size limit",
            path=oversized_file.path,
            actual_field="size_bytes",
            actual_value=oversized_file.size_bytes,
            limit_field="max_size_bytes",
            limit_value=config.TRACECAT__MAX_SKILL_FILE_SIZE_BYTES,
        )

    total_size_bytes = sum(file.size_bytes for file in files)

    if total_size_bytes > config.TRACECAT__MAX_SKILL_TOTAL_SIZE_BYTES:
        return SkillFileLimitViolation(
            code="skill_total_size_limit_exceeded",
            message="Skill draft exceeds the aggregate size limit",
            path=None,
            actual_field="total_size_bytes",
            actual_value=total_size_bytes,
            limit_field="max_total_size_bytes",
            limit_value=config.TRACECAT__MAX_SKILL_TOTAL_SIZE_BYTES,
        )
    return None


def normalize_skill_path(path: str) -> str:
    """Normalize and validate a relative POSIX draft path.

    Args:
        path: User-provided file path.

    Returns:
        The normalized relative POSIX path.

    Raises:
        TracecatValidationError: If the path is empty, absolute, or escapes
            the skill root.
    """

    if "\\" in path:
        raise TracecatValidationError(
            f"Skill paths must use POSIX separators: {path!r}",
            detail={"code": "invalid_path", "path": path},
        )

    path_obj = PurePosixPath(path)
    normalized = str(path_obj)
    if normalized in {"", "."}:
        raise TracecatValidationError(
            "Skill path cannot be empty",
            detail={"code": "invalid_path", "path": path},
        )
    if path_obj.is_absolute() or ".." in path_obj.parts:
        raise TracecatValidationError(
            f"Skill path cannot escape the skill root: {path!r}",
            detail={"code": "invalid_path", "path": path},
        )
    if normalized != path:
        raise TracecatValidationError(
            f"Skill path must already be normalized: {path!r}",
            detail={"code": "invalid_path", "path": path},
        )
    return normalized
