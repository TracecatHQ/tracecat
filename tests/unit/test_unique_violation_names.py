"""Every constraint passed to ``is_unique_violation`` must exist in the schema."""

import ast
from pathlib import Path

from sqlalchemy import PrimaryKeyConstraint, UniqueConstraint

from tracecat.db.common import DBConstraints
from tracecat.db.models import Base

REPO_ROOT = Path(__file__).resolve().parents[2]
SOURCE_ROOTS = (REPO_ROOT / "tracecat", REPO_ROOT / "packages" / "tracecat-ee")


def _schema_unique_names() -> set[str]:
    names: set[str] = set()
    for table in Base.metadata.tables.values():
        for constraint in table.constraints:
            if isinstance(constraint, UniqueConstraint | PrimaryKeyConstraint):
                names.add(str(constraint.name))
        names.update(str(index.name) for index in table.indexes if index.unique)
    return names


def _module_constants(tree: ast.Module) -> dict[str, str]:
    return {
        target.id: node.value.value
        for node in tree.body
        if isinstance(node, ast.Assign)
        and isinstance(node.value, ast.Constant)
        and isinstance(node.value.value, str)
        for target in node.targets
        if isinstance(target, ast.Name)
    }


def _constraint_arguments() -> dict[str, str]:
    found: dict[str, str] = {}
    for root in SOURCE_ROOTS:
        for path in root.rglob("*.py"):
            tree = ast.parse(path.read_text())
            constants = _module_constants(tree)
            for node in ast.walk(tree):
                if not (
                    isinstance(node, ast.Call)
                    and isinstance(node.func, ast.Name)
                    and node.func.id == "is_unique_violation"
                ):
                    continue
                for arg in node.args[1:]:
                    site = f"{path.relative_to(REPO_ROOT)}:{arg.lineno}"
                    match arg:
                        case ast.Constant(value=str(name)):
                            found[name] = site
                        case ast.Attribute(
                            value=ast.Name(id="DBConstraints"), attr=attr
                        ):
                            found[DBConstraints[attr].value] = site
                        case ast.Name(id=name) if name in constants:
                            found[constants[name]] = site
                        case _:
                            raise AssertionError(f"Unchecked constraint at {site}")
    return found


def test_unique_violation_constraint_names_exist() -> None:
    used = _constraint_arguments()
    assert used, "expected is_unique_violation call sites"
    schema = _schema_unique_names()
    missing = {name: site for name, site in used.items() if name not in schema}
    assert not missing, f"Unknown constraint names: {missing}"
