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


def _constraint_arguments() -> dict[str, str]:
    found: dict[str, str] = {}
    for root in SOURCE_ROOTS:
        for path in root.rglob("*.py"):
            for node in ast.walk(ast.parse(path.read_text())):
                if not (
                    isinstance(node, ast.Call)
                    and isinstance(node.func, ast.Name)
                    and node.func.id == "is_unique_violation"
                ):
                    continue
                for arg in node.args[1:]:
                    match arg:
                        case ast.Constant(value=str(name)):
                            found[name] = f"{path.relative_to(REPO_ROOT)}:{arg.lineno}"
                        case ast.Attribute(
                            value=ast.Name(id="DBConstraints"), attr=attr
                        ):
                            found[DBConstraints[attr].value] = str(path)
    return found


def test_unique_violation_constraint_names_exist() -> None:
    used = _constraint_arguments()
    assert used, "expected is_unique_violation call sites"
    schema = _schema_unique_names()
    missing = {name: site for name, site in used.items() if name not in schema}
    assert not missing, f"Unknown constraint names: {missing}"
