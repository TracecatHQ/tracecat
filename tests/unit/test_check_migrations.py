"""Exercise real Alembic revision graphs without application or DB fixtures."""

import shutil
from pathlib import Path

import pytest
from alembic.script import ScriptDirectory
from alembic.script.revision import RevisionError
from alembic.util import CommandError
from check_migrations import LINEAR_HISTORY_BASE, check_migrations, main

type Parent = str | tuple[str, ...] | None


def write_revision(
    directory: Path,
    revision: str,
    parent: Parent,
    *,
    depends_on: Parent = None,
    branch_labels: Parent = None,
    filename: str | None = None,
) -> None:
    versions = directory / "versions"
    versions.mkdir(exist_ok=True)
    (versions / f"{filename or revision}.py").write_text(
        f"revision = {revision!r}\n"
        f"down_revision = {parent!r}\n"
        f"depends_on = {depends_on!r}\n"
        f"branch_labels = {branch_labels!r}\n"
    )


@pytest.fixture
def history(tmp_path: Path) -> Path:
    write_revision(tmp_path, "root", None)
    write_revision(tmp_path, "left", "root")
    write_revision(tmp_path, "right", "root")
    write_revision(tmp_path, "baseline", ("left", "right"))
    # Loading revision metadata must never execute the migration environment.
    (tmp_path / "env.py").write_text("raise AssertionError('env.py executed')\n")
    return tmp_path


@pytest.mark.parametrize("new_revisions", [0, 1, 3])
def test_accepts_linear_extensions_of_historical_merge(
    history: Path, new_revisions: int
) -> None:
    head = "baseline"
    for index in range(new_revisions):
        revision = f"new_{index}"
        write_revision(history, revision, head)
        head = revision
    assert (
        check_migrations(ScriptDirectory(str(history)), linear_since="baseline") == head
    )


@pytest.mark.parametrize("parent", ["baseline", "root"])
def test_rejects_forks_even_from_historical_revisions(
    history: Path, parent: str
) -> None:
    write_revision(history, "first", "baseline")
    write_revision(history, "second", parent)
    with pytest.raises(ValueError, match="exactly one Alembic head"):
        check_migrations(ScriptDirectory(str(history)), linear_since="baseline")


def test_rejects_new_merge_even_with_one_head(history: Path) -> None:
    write_revision(history, "first", "baseline")
    write_revision(history, "second", "baseline")
    write_revision(history, "merged", ("first", "second"))
    scripts = ScriptDirectory(str(history))
    assert scripts.get_heads() == ["merged"]
    with pytest.raises(ValueError, match="New merge revisions are not allowed"):
        check_migrations(scripts, linear_since="baseline")


def test_rejects_new_cross_branch_dependencies(history: Path) -> None:
    write_revision(history, "new", "baseline", depends_on="left")
    with pytest.raises(ValueError, match="uses depends_on"):
        check_migrations(ScriptDirectory(str(history)), linear_since="baseline")


def test_accepts_historical_dependencies(history: Path) -> None:
    write_revision(history, "right", "root", depends_on="left")
    assert (
        check_migrations(ScriptDirectory(str(history)), linear_since="baseline")
        == "baseline"
    )


def test_rejects_multiple_roots_even_if_merged(history: Path) -> None:
    write_revision(history, "right", None)
    with pytest.raises(ValueError, match="exactly one Alembic base"):
        check_migrations(ScriptDirectory(str(history)), linear_since="baseline")


def test_rejects_empty_history(tmp_path: Path) -> None:
    (tmp_path / "versions").mkdir()
    with pytest.raises(ValueError, match="found 0"):
        check_migrations(ScriptDirectory(str(tmp_path)), linear_since="baseline")


def test_rejects_missing_policy_boundary(history: Path) -> None:
    with pytest.raises(CommandError, match="missing"):
        check_migrations(ScriptDirectory(str(history)), linear_since="missing")


def test_rejects_missing_parent(history: Path) -> None:
    write_revision(history, "new", "missing")
    with pytest.raises(UserWarning, match="not present"):
        check_migrations(ScriptDirectory(str(history)), linear_since="baseline")


def test_rejects_duplicate_revision_ids(history: Path) -> None:
    write_revision(history, "baseline", ("left", "right"), filename="duplicate")
    with pytest.raises(UserWarning, match="present more than once"):
        check_migrations(ScriptDirectory(str(history)), linear_since="baseline")


def test_rejects_cycles(history: Path) -> None:
    write_revision(history, "first", "second")
    write_revision(history, "second", "first")
    with pytest.raises(RevisionError, match="Cycle"):
        check_migrations(ScriptDirectory(str(history)), linear_since="baseline")


def test_cli_fails_for_invalid_history(
    history: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    (history / "alembic.ini").write_text("[alembic]\nscript_location = .\n")
    write_revision(history, "first", "baseline")
    write_revision(history, "second", "baseline")
    monkeypatch.chdir(history)
    assert main([]) == 1
    assert "Expected exactly one Alembic head" in capsys.readouterr().err


def test_cli_accepts_valid_history(
    history: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    (history / "alembic.ini").write_text("[alembic]\nscript_location = .\n")
    write_revision(history, LINEAR_HISTORY_BASE, "baseline")
    monkeypatch.chdir(history)
    assert main([]) == 0
    assert "Alembic migration history OK" in capsys.readouterr().out


@pytest.fixture
def base_history(history: Path, tmp_path_factory: pytest.TempPathFactory) -> Path:
    base = tmp_path_factory.mktemp("base") / "alembic"
    shutil.copytree(history, base)
    return base


@pytest.mark.parametrize("new_revisions", [0, 1, 3])
def test_base_comparison_accepts_append_only_history(
    history: Path, base_history: Path, new_revisions: int
) -> None:
    head = "baseline"
    for index in range(new_revisions):
        revision = f"new_{index}"
        write_revision(history, revision, head)
        head = revision
    assert (
        check_migrations(
            ScriptDirectory(str(history)),
            linear_since="baseline",
            base=ScriptDirectory(str(base_history)),
        )
        == head
    )


@pytest.mark.parametrize("child,parent", [("left", "root"), ("deployed", "baseline")])
def test_rejects_insertions_behind_existing_head(
    history: Path, base_history: Path, child: str, parent: str
) -> None:
    # Cover both grandfathered history and the new linear chain.
    write_revision(base_history, "deployed", "baseline")
    write_revision(history, "deployed", "baseline")
    write_revision(history, "inserted", parent)
    write_revision(history, child, "inserted")
    scripts = ScriptDirectory(str(history))
    assert check_migrations(scripts, linear_since="baseline") == "deployed"
    assert list(scripts.iterate_revisions("heads", "deployed")) == []
    with pytest.raises(ValueError, match=f"{child} changed down_revision"):
        check_migrations(
            scripts,
            linear_since="baseline",
            base=ScriptDirectory(str(base_history)),
        )


@pytest.mark.parametrize("rename", [False, True])
def test_rejects_removed_or_renamed_existing_revision(
    history: Path, base_history: Path, rename: bool
) -> None:
    write_revision(base_history, "deployed", "baseline")
    if rename:
        write_revision(history, "renamed", "baseline")
    with pytest.raises(ValueError, match="deployed was removed or renamed"):
        check_migrations(
            ScriptDirectory(str(history)),
            linear_since="baseline",
            base=ScriptDirectory(str(base_history)),
        )


@pytest.mark.parametrize("field", ["depends_on", "branch_labels"])
def test_rejects_changed_historical_metadata(
    history: Path, base_history: Path, field: str
) -> None:
    write_revision(
        history,
        "right",
        "root",
        depends_on="left" if field == "depends_on" else None,
        branch_labels="changed" if field == "branch_labels" else None,
    )
    with pytest.raises(ValueError, match=f"right changed {field}"):
        check_migrations(
            ScriptDirectory(str(history)),
            linear_since="baseline",
            base=ScriptDirectory(str(base_history)),
        )


def test_accepts_new_label_without_treating_inherited_labels_as_rewrites(
    history: Path, base_history: Path
) -> None:
    write_revision(history, "new", "baseline", branch_labels="new_label")
    assert (
        check_migrations(
            ScriptDirectory(str(history)),
            linear_since="baseline",
            base=ScriptDirectory(str(base_history)),
        )
        == "new"
    )


@pytest.mark.parametrize("rewrite", [None, "metadata", "body"])
def test_cli_compares_base_history(
    history: Path,
    base_history: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
    rewrite: str | None,
) -> None:
    (history / "alembic.ini").write_text("[alembic]\nscript_location = .\n")
    write_revision(history, LINEAR_HISTORY_BASE, "baseline")
    write_revision(base_history, LINEAR_HISTORY_BASE, "baseline")
    if rewrite == "metadata":
        write_revision(history, "inserted", "baseline")
        write_revision(history, LINEAR_HISTORY_BASE, "inserted")
    elif rewrite == "body":
        with (history / "versions" / f"{LINEAR_HISTORY_BASE}.py").open("a") as file:
            file.write("def upgrade():\n    raise RuntimeError('changed operation')\n")
    monkeypatch.chdir(history)
    assert main(["--base-dir", str(base_history)]) == (1 if rewrite else 0)
    output = capsys.readouterr()
    if rewrite == "metadata":
        assert "changed down_revision" in output.err
    elif rewrite == "body":
        assert "changed file contents" in output.err
    else:
        assert "Alembic migration history OK" in output.out


def test_cli_rejects_missing_base_directory(
    history: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    (history / "alembic.ini").write_text("[alembic]\nscript_location = .\n")
    monkeypatch.chdir(history)
    assert main(["--base-dir", str(history / "missing")]) == 1
    assert "Path doesn't exist" in capsys.readouterr().err


@pytest.mark.parametrize("revision", ["root", "deployed"])
@pytest.mark.parametrize(
    "body",
    [
        "def upgrade():\n    raise RuntimeError('changed upgrade')\n",
        "def downgrade():\n    raise RuntimeError('changed downgrade')\n",
        "# Formatting and comment edits are also rejected.\n\n",
    ],
)
def test_rejects_changed_revision_contents(
    history: Path, base_history: Path, revision: str, body: str
) -> None:
    for directory in (history, base_history):
        write_revision(directory, "deployed", "baseline")
        with (directory / "versions" / f"{revision}.py").open("a") as file:
            file.write("def upgrade():\n    pass\ndef downgrade():\n    pass\n")
    with (history / "versions" / f"{revision}.py").open("a") as file:
        file.write(body)
    with pytest.raises(ValueError, match=f"{revision} changed file contents"):
        check_migrations(
            ScriptDirectory(str(history)),
            linear_since="baseline",
            base=ScriptDirectory(str(base_history)),
        )


def test_accepts_unchanged_bodies_and_new_migration_operations(
    history: Path, base_history: Path
) -> None:
    body = "def upgrade():\n    raise AssertionError('must not execute operations')\n"
    for directory in (history, base_history):
        with (directory / "versions" / "baseline.py").open("a") as file:
            file.write(body)
    write_revision(history, "new", "baseline")
    with (history / "versions" / "new.py").open("a") as file:
        file.write(body)
    assert (
        check_migrations(
            ScriptDirectory(str(history)),
            linear_since="baseline",
            base=ScriptDirectory(str(base_history)),
        )
        == "new"
    )
