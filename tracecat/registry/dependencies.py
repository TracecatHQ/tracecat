"""Data models for dependency conflict parsing and error handling.

This module provides structured data models using Pydantic to replace
the loose dictionary-based approach for dependency conflict information.
"""

from __future__ import annotations

import re
from enum import StrEnum
from typing import Any

from pydantic import BaseModel, Field, field_validator, model_validator

from tracecat.exceptions import RegistryError


class VersionOperator(StrEnum):
    """Supported version constraint operators."""

    EQUAL = "=="
    GREATER_THAN = ">"
    GREATER_EQUAL = ">="
    LESS_THAN = "<"
    LESS_EQUAL = "<="
    NOT_EQUAL = "!="
    COMPATIBLE = "~="
    CARET = "^"


class ConflictStatementType(StrEnum):
    """Types of conflict statements in dependency resolution errors."""

    BECAUSE = "because"
    AND_BECAUSE = "and_because"
    CONCLUSION = "conclusion"


class DependencyRelationship(StrEnum):
    """Types of dependency relationships."""

    REQUIRES = "requires"
    DEPENDS = "depends"
    CONFLICTS = "conflicts"


class VersionConstraint(BaseModel):
    """Represents a version constraint for a package dependency."""

    operator: VersionOperator
    version: str

    def __str__(self) -> str:
        return f"{self.operator.value}{self.version}"

    @classmethod
    def parse(cls, constraint_str: str) -> VersionConstraint | None:
        """Parse a version constraint string into a VersionConstraint object.

        Args:
            constraint_str: String like ">=1.0.0", "==2.1.0", etc.

        Returns:
            VersionConstraint object or None if parsing fails
        """
        # Pattern to match operator and version - ensure version is not empty
        pattern = r"^(==|>=|<=|!=|~=|\^|>|<)([^=<>!~\^].*)$"
        match = re.match(pattern, constraint_str.strip())

        if not match:
            return None

        operator_str, version = match.groups()

        try:
            operator = VersionOperator(operator_str)
            return cls(operator=operator, version=version.strip())
        except ValueError:
            return None


class DependencyInfo(BaseModel):
    """Information about a single dependency requirement."""

    package: str
    relationship: DependencyRelationship
    constraint: VersionConstraint | None = None
    depends_on: str | None = None

    @model_validator(mode="after")
    def validate_depends_on(self) -> DependencyInfo:
        """Validate that depends_on is provided when relationship is 'depends'."""
        if self.relationship == DependencyRelationship.DEPENDS and not self.depends_on:
            raise ValueError("depends_on is required when relationship is 'depends'")
        return self


class ConflictStatement(BaseModel):
    """Represents a parsed conflict statement from dependency resolution errors."""

    type: ConflictStatementType
    statement: str
    dependencies: list[DependencyInfo] = Field(default_factory=list)
    index: int = 0


class VersionConflict(BaseModel):
    """Information about version conflicts for a specific package."""

    package: str
    required_versions: list[str] = Field(default_factory=list)

    @field_validator("required_versions", mode="after")
    @classmethod
    def sort_versions(cls, v: list[str]) -> list[str]:
        """Sort required versions for consistent output."""
        return sorted(v)


class ConflictSummary(BaseModel):
    """High-level summary of dependency conflicts."""

    conflicting_packages: list[str] = Field(default_factory=list)
    version_conflicts: list[VersionConflict] = Field(default_factory=list)


class DependencyConflictResult(BaseModel):
    """Complete result from parsing dependency conflict error messages."""

    found: bool
    conflicts: list[ConflictStatement] = Field(default_factory=list)
    summary: ConflictSummary = Field(default_factory=ConflictSummary)
    raw_error: str = ""

    def to_dict(self) -> dict[str, Any]:
        """Convert to dictionary format for backward compatibility.

        Returns:
            Dictionary representation matching the old format
        """
        return self.model_dump(
            mode="python", by_alias=True, exclude_none=True, serialize_as_any=True
        )


class RegistryDependencyConflictError(RegistryError):
    """Exception raised when dependency conflicts are detected during repository installation."""

    def __init__(
        self,
        message: str,
        conflicts: DependencyConflictResult | None = None,
        **kwargs,
    ):
        super().__init__(message, **kwargs)
        self.conflicts = conflicts
        if conflicts:
            self.detail = {"conflicts": conflicts.to_dict()}


def parse_dependency_conflicts_dict(
    error_message: str,
) -> dict[str, Any] | None:
    """
    Parse dependency conflicts and return dictionary format for backward compatibility.

    Args:
        error_message: The error message containing dependency conflicts

    Returns:
        Dictionary format of the parsed conflicts or None
    """
    result = parse_dependency_conflicts(error_message)
    return result.to_dict() if result else None


def parse_dependency_conflicts(
    error_message: str,
) -> DependencyConflictResult | None:
    """
    Parse dependency conflicts from package manager error messages.

    Args:
        error_message: The error message containing dependency conflicts

    Returns:
        DependencyConflictResult object with parsed conflict information,
        or None if the target message is not found.
    """
    # Check if the error message contains the target string
    if "No solution found when resolving dependencies for split" not in error_message:
        return None

    conflicts = []

    # Split the message into lines for easier parsing
    lines = error_message.strip().split("\n")

    # Find the start of the conflict explanation (after the "No solution found" line)
    conflict_section_started = False
    conflict_text = []

    for line in lines:
        if "No solution found when resolving dependencies for split" in line:
            conflict_section_started = True
            continue

        if conflict_section_started:
            # Stop when we reach the help section
            if line.strip().startswith("help:"):
                break
            # Skip the hint section but include everything else
            if not line.strip().startswith("hint:"):
                conflict_text.append(line)

    # Join the conflict text for parsing
    full_conflict_text = "\n".join(conflict_text)

    # Parse "Because" statements - these contain the actual conflicts
    because_pattern = r"Because\s+(.+?)(?=(?:Because|And because|we can conclude|$))"
    because_matches = re.findall(
        because_pattern, full_conflict_text, re.DOTALL | re.IGNORECASE
    )

    # Parse "And because" statements
    and_because_pattern = (
        r"And because\s+(.+?)(?=(?:Because|And because|we can conclude|$))"
    )
    and_because_matches = re.findall(
        and_because_pattern, full_conflict_text, re.DOTALL | re.IGNORECASE
    )

    # Parse conclusions
    conclusion_pattern = (
        r"we can conclude that\s+(.+?)(?=(?:Because|And because|we can conclude|$))"
    )
    conclusions = re.findall(
        conclusion_pattern, full_conflict_text, re.DOTALL | re.IGNORECASE
    )

    # Extract specific dependency information - enhanced to support multiple operators
    dep_pattern = r"(\S+)\s*(==|>=|<=|!=|~=|\^|>|<)\s*([^\s,]+)"
    depends_pattern = (
        r"(\S+)\s+depends on\s+(\S+)(?:\s*(==|>=|<=|!=|~=|\^|>|<)\s*([^\s,]+))?"
    )

    # Process all conflict statements
    all_statements: list[ConflictStatement] = []

    for idx, statement in enumerate(because_matches):
        all_statements.append(
            ConflictStatement(
                type=ConflictStatementType.BECAUSE,
                statement=statement.strip(),
                index=idx,
            )
        )

    for idx, statement in enumerate(and_because_matches):
        all_statements.append(
            ConflictStatement(
                type=ConflictStatementType.AND_BECAUSE,
                statement=statement.strip(),
                index=idx,
            )
        )

    for idx, conclusion in enumerate(conclusions):
        all_statements.append(
            ConflictStatement(
                type=ConflictStatementType.CONCLUSION,
                statement=conclusion.strip(),
                index=idx,
            )
        )

    # Extract dependency relationships
    for statement_obj in all_statements:
        # Look for version constraints
        version_matches = re.findall(dep_pattern, statement_obj.statement)
        depends_matches = re.findall(depends_pattern, statement_obj.statement)

        # Add direct version specifications
        for package, operator, version in version_matches:
            if package not in ["is", "are", "be"]:  # Filter out common words
                constraint = VersionConstraint.parse(f"{operator}{version}")
                if constraint:
                    dep_info = DependencyInfo(
                        package=package,
                        relationship=DependencyRelationship.REQUIRES,
                        constraint=constraint,
                    )
                    statement_obj.dependencies.append(dep_info)

        # Add dependency relationships
        for match in depends_matches:
            if len(match) == 4:
                dependent, dependency, operator, version = match
                constraint = (
                    VersionConstraint.parse(f"{operator}{version}")
                    if operator and version
                    else None
                )
            else:
                dependent, dependency = match[:2]
                constraint = None

            dep_info = DependencyInfo(
                package=dependent,
                depends_on=dependency,
                relationship=DependencyRelationship.DEPENDS,
                constraint=constraint,
            )
            statement_obj.dependencies.append(dep_info)

    # Filter out statements without dependencies (except conclusions)
    conflicts = [
        stmt
        for stmt in all_statements
        if stmt.dependencies or stmt.type == ConflictStatementType.CONCLUSION
    ]

    # Extract the main conflict summary
    summary = extract_conflict_summary(full_conflict_text)

    return DependencyConflictResult(
        found=True,
        conflicts=conflicts,
        summary=summary,
        raw_error=error_message,
    )


def extract_conflict_summary(conflict_text: str) -> ConflictSummary:
    """
    Extract a high-level summary of the conflicts.

    Args:
        conflict_text: The full conflict text

    Returns:
        ConflictSummary object with the main conflicts
    """
    conflicting_packages: list[str] = []
    version_conflicts: list[VersionConflict] = []

    # Find all package version requirements - enhanced to support multiple operators
    version_pattern = r"(\S+)\s*(==|>=|<=|!=|~=|\^|>|<)\s*([^\s,]+)"
    all_versions = re.findall(version_pattern, conflict_text)

    # Group by package to find conflicts
    package_versions: dict[str, set[str]] = {}
    for package, operator, version in all_versions:
        if package not in ["is", "are", "be", "and", "or"]:  # Filter common words
            if package not in package_versions:
                package_versions[package] = set()
            # Clean version string (remove trailing punctuation) and include operator
            clean_version = f"{operator}{version.rstrip('.,;:')}"
            package_versions[package].add(clean_version)

    # Identify packages with multiple version requirements
    for package, versions in package_versions.items():
        if len(versions) > 1:
            conflicting_packages.append(package)
            version_conflicts.append(
                VersionConflict(
                    package=package,
                    required_versions=list(versions),
                )
            )

    return ConflictSummary(
        conflicting_packages=conflicting_packages,
        version_conflicts=version_conflicts,
    )


def get_conflict_summary(error_message: str) -> str | None:
    """
    Get a simplified toast-friendly message about dependency conflicts.

    Args:
        error_message: The error message containing dependency conflicts

    Returns:
        A formatted string suitable for toast notifications, or None if no conflicts found
    """
    result = parse_dependency_conflicts(error_message)
    if not result:
        return None

    if not result.summary.version_conflicts:
        return None

    lines = ["Version conflicts:"]

    for conflict in result.summary.version_conflicts:
        version_str = " vs ".join(conflict.required_versions)
        lines.append(f"  - {conflict.package}: {version_str}")

    return "\n".join(lines)
