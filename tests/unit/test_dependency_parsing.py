"""Tests for dependency conflict parsing and error handling."""

import pytest

from tracecat.registry.dependencies import (
    ConflictStatement,
    ConflictStatementType,
    ConflictSummary,
    DependencyConflictResult,
    DependencyInfo,
    DependencyRelationship,
    VersionConflict,
    VersionConstraint,
    VersionOperator,
    extract_conflict_summary,
    get_conflict_summary,
    parse_dependency_conflicts,
    parse_dependency_conflicts_dict,
)


class TestVersionConstraint:
    """Test VersionConstraint parsing and functionality."""

    @pytest.mark.parametrize(
        "constraint_str,expected_operator,expected_version",
        [
            ("==1.0.0", VersionOperator.EQUAL, "1.0.0"),
            (">=2.1.0", VersionOperator.GREATER_EQUAL, "2.1.0"),
            ("<=3.0.0", VersionOperator.LESS_EQUAL, "3.0.0"),
            (">4.0.0", VersionOperator.GREATER_THAN, "4.0.0"),
            ("<5.0.0", VersionOperator.LESS_THAN, "5.0.0"),
            ("!=1.5.0", VersionOperator.NOT_EQUAL, "1.5.0"),
            ("~=1.2.0", VersionOperator.COMPATIBLE, "1.2.0"),
            ("^1.0.0", VersionOperator.CARET, "1.0.0"),
        ],
    )
    def test_parse_valid_constraints(
        self, constraint_str, expected_operator, expected_version
    ):
        constraint = VersionConstraint.parse(constraint_str)
        assert constraint is not None
        assert constraint.operator == expected_operator
        assert constraint.version == expected_version
        assert str(constraint) == constraint_str

    @pytest.mark.parametrize(
        "invalid_constraint",
        [
            "1.0.0",  # No operator
            "===1.0.0",  # Invalid operator
            "=>1.0.0",  # Invalid operator
            "==",  # No version
            "",  # Empty string
        ],
    )
    def test_parse_invalid_constraints(self, invalid_constraint):
        constraint = VersionConstraint.parse(invalid_constraint)
        assert constraint is None


class TestDependencyInfo:
    """Test DependencyInfo validation."""

    def test_valid_dependency_info(self):
        # Test with constraint
        dep = DependencyInfo(
            package="requests",
            relationship=DependencyRelationship.REQUIRES,
            constraint=VersionConstraint(
                operator=VersionOperator.EQUAL, version="2.28.0"
            ),
        )
        assert dep.package == "requests"
        assert dep.relationship == DependencyRelationship.REQUIRES
        assert dep.constraint is not None
        assert dep.constraint.version == "2.28.0"

    def test_depends_relationship_validation(self):
        # Valid depends relationship
        dep = DependencyInfo(
            package="mypackage",
            relationship=DependencyRelationship.DEPENDS,
            depends_on="otherpackage",
        )
        assert dep.depends_on == "otherpackage"

        # Invalid depends relationship (missing depends_on)
        with pytest.raises(ValueError, match="depends_on is required"):
            DependencyInfo(
                package="mypackage",
                relationship=DependencyRelationship.DEPENDS,
            )


class TestDependencyConflictParsing:
    """Test parsing of dependency conflict error messages."""

    def test_parse_simple_conflict(self):
        error_message = """
        No solution found when resolving dependencies for split (sys):
          Because mypackage==1.0.0 depends on requests==2.28.0
          and yourpackage==2.0.0 depends on requests==2.30.0,
          we can conclude that mypackage==1.0.0 and yourpackage==2.0.0 are incompatible.
        """

        result = parse_dependency_conflicts(error_message)
        assert result is not None
        assert result.found is True
        assert len(result.conflicts) > 0

        # Check that we found the conflict
        assert len(result.summary.version_conflicts) == 1
        conflict = result.summary.version_conflicts[0]
        assert conflict.package == "requests"
        assert set(conflict.required_versions) == {"==2.28.0", "==2.30.0"}

    def test_parse_complex_conflict_with_operators(self):
        error_message = """
        No solution found when resolving dependencies for split (sys):
          Because package-a>=1.0.0 depends on numpy>=1.20.0
          and package-b<=2.0.0 depends on numpy<1.20.0,
          we can conclude that package-a>=1.0.0 and package-b<=2.0.0 are incompatible.
        """

        result = parse_dependency_conflicts(error_message)
        assert result is not None
        assert result.found is True

        # Check version constraints were parsed correctly
        dependencies = []
        for conflict in result.conflicts:
            dependencies.extend(conflict.dependencies)

        # Find numpy dependencies
        numpy_deps = [d for d in dependencies if d.package == "numpy"]
        assert len(numpy_deps) >= 1

        # Check that different operators were parsed
        operators_found = {
            str(d.constraint.operator) if d.constraint else None for d in numpy_deps
        }
        assert None not in operators_found

    def test_parse_no_conflict(self):
        error_message = "Some other error that is not a dependency conflict"
        result = parse_dependency_conflicts(error_message)
        assert result is None

    def test_backward_compatibility(self):
        error_message = """
        No solution found when resolving dependencies for split (sys):
          Because mypackage==1.0.0 depends on requests==2.28.0,
          we can conclude that mypackage==1.0.0 requires requests==2.28.0.
        """

        # Test new function returns structured result
        result = parse_dependency_conflicts(error_message)
        assert isinstance(result, DependencyConflictResult)

        # Test backward compatibility function returns dict
        dict_result = parse_dependency_conflicts_dict(error_message)
        assert isinstance(dict_result, dict)
        assert dict_result["found"] is True
        assert "conflicts" in dict_result
        assert "summary" in dict_result


class TestConflictSummary:
    """Test conflict summary extraction."""

    def test_extract_conflict_summary(self):
        conflict_text = """
        Because package-a==1.0.0 depends on requests==2.28.0
        and package-b==2.0.0 depends on requests==2.30.0,
        and package-c==3.0.0 depends on urllib3>=1.26.0
        and package-d==4.0.0 depends on urllib3<=1.25.0
        """

        summary = extract_conflict_summary(conflict_text)
        assert isinstance(summary, ConflictSummary)

        # Should find two conflicting packages
        assert len(summary.version_conflicts) == 2

        # Check requests conflict
        requests_conflict = next(
            (vc for vc in summary.version_conflicts if vc.package == "requests"), None
        )
        assert requests_conflict is not None
        assert set(requests_conflict.required_versions) == {"==2.28.0", "==2.30.0"}

        # Check urllib3 conflict
        urllib_conflict = next(
            (vc for vc in summary.version_conflicts if vc.package == "urllib3"), None
        )
        assert urllib_conflict is not None
        assert set(urllib_conflict.required_versions) == {">=1.26.0", "<=1.25.0"}


class TestToastMessage:
    """Test toast message generation."""

    def test_get_toast_message_with_conflicts(self):
        error_message = """
        No solution found when resolving dependencies for split (sys):
          Because mypackage==1.0.0 depends on requests==2.28.0
          and yourpackage==2.0.0 depends on requests==2.30.0,
          we can conclude that they are incompatible.
        """

        message = get_conflict_summary(error_message)
        assert message is not None
        assert "Version conflicts:" in message
        assert "requests:" in message
        assert "==2.28.0 vs ==2.30.0" in message

    def test_get_toast_message_no_conflicts(self):
        error_message = "Some other error"
        message = get_conflict_summary(error_message)
        assert message is None

    def test_get_toast_message_multiple_conflicts(self):
        error_message = """
        No solution found when resolving dependencies for split (sys):
          Because package-a depends on requests==2.28.0 and urllib3>=1.26.0
          and package-b depends on requests==2.30.0 and urllib3<=1.25.0,
          we can conclude that they are incompatible.
        """

        message = get_conflict_summary(error_message)
        assert message is not None
        lines = message.split("\n")
        assert len(lines) >= 3  # Header + at least 2 conflicts
        assert any("requests" in line for line in lines)
        assert any("urllib3" in line for line in lines)


class TestDependencyModels:
    """Test the Pydantic models directly."""

    def test_conflict_statement_model(self):
        stmt = ConflictStatement(
            type=ConflictStatementType.BECAUSE,
            statement="Because package depends on something",
            dependencies=[
                DependencyInfo(
                    package="package",
                    relationship=DependencyRelationship.REQUIRES,
                    constraint=VersionConstraint(
                        operator=VersionOperator.EQUAL, version="1.0.0"
                    ),
                )
            ],
            index=0,
        )
        assert stmt.type == ConflictStatementType.BECAUSE
        assert len(stmt.dependencies) == 1

    def test_version_conflict_sorting(self):
        vc = VersionConflict(
            package="requests", required_versions=["2.30.0", "2.28.0", "2.29.0"]
        )
        # Should be sorted after validation
        assert vc.required_versions == ["2.28.0", "2.29.0", "2.30.0"]

    def test_dependency_conflict_result_to_dict(self):
        result = DependencyConflictResult(
            found=True,
            conflicts=[
                ConflictStatement(
                    type=ConflictStatementType.BECAUSE,
                    statement="test statement",
                    dependencies=[
                        DependencyInfo(
                            package="pkg",
                            relationship=DependencyRelationship.REQUIRES,
                            constraint=VersionConstraint(
                                operator=VersionOperator.EQUAL, version="1.0.0"
                            ),
                        )
                    ],
                )
            ],
            summary=ConflictSummary(
                conflicting_packages=["pkg"],
                version_conflicts=[
                    VersionConflict(package="pkg", required_versions=["1.0.0", "2.0.0"])
                ],
            ),
            raw_error="test error",
        )

        dict_result = result.to_dict()
        assert dict_result["found"] is True
        assert len(dict_result["conflicts"]) == 1
        assert dict_result["conflicts"][0]["type"] == "because"
        assert len(dict_result["summary"]["version_conflicts"]) == 1

    def test_parse_real_world_complex_conflict(self):
        """Test parsing a real-world complex dependency conflict from log output."""
        error_message = """
        Failed to install repository: Updating ssh://git@github.com/TracecatHQ/internal-registry.git (0c6d4e00cba5f309e208dee11994f3ddbeb1bc42)
        Updated ssh://git@github.com/TracecatHQ/internal-registry.git (0c6d4e00cba5f309e208dee11994f3ddbeb1bc42)
        × No solution found when resolving dependencies for split
        │ (python_full_version >= '3.14'):
        ╰─▶ Because only custom-actions==0.1.0 is available and
            custom-actions==0.1.0 depends on pydantic==1.10.22, we can conclude that
            all versions of custom-actions depend on pydantic==1.10.22.
            And because tracecat depends on custom-actions, we can conclude that
            tracecat depends on pydantic==1.10.22.
            And because tracecat depends on pydantic==2.10.6 and your workspace
            requires tracecat, we can conclude that your workspace's requirements
            are unsatisfiable.

            hint: While the active Python version is 3.12, the resolution failed for
            other Python versions supported by your project. Consider limiting your
            project's supported Python versions using `requires-python`.
        help: If you want to add the package regardless of the failed resolution,
              provide the `--frozen` flag to skip locking and syncing.
        """

        # Parse the dependency conflicts
        result = parse_dependency_conflicts(error_message)
        assert result is not None
        assert result.found is True

        # Check that we found conflicts
        assert len(result.conflicts) > 0

        # Verify pydantic version conflict is detected
        assert len(result.summary.version_conflicts) == 1
        conflict = result.summary.version_conflicts[0]
        assert conflict.package == "pydantic"
        # Should find both version requirements
        assert "==1.10.22" in conflict.required_versions
        assert "==2.10.6" in conflict.required_versions

        # Check that the main conflicting packages are identified
        assert "pydantic" in result.summary.conflicting_packages

        # Verify specific conflict statements are parsed
        because_statements = [
            c for c in result.conflicts if c.type == ConflictStatementType.BECAUSE
        ]
        and_because_statements = [
            c for c in result.conflicts if c.type == ConflictStatementType.AND_BECAUSE
        ]

        # Should have at least one "because" and one "and because" statement
        assert len(because_statements) >= 1
        assert len(and_because_statements) >= 1

        # Check that dependencies are extracted correctly
        all_deps = []
        for conflict in result.conflicts:
            all_deps.extend(conflict.dependencies)

        # Should find custom-actions and pydantic packages in dependencies
        package_names = {dep.package for dep in all_deps}
        assert "custom-actions" in package_names
        assert "pydantic" in package_names
