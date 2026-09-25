"""Guard against shipping a migration graph that ``alembic upgrade head`` rejects."""

from pathlib import Path

from alembic.config import Config
from alembic.script import ScriptDirectory

REPO_ROOT = Path(__file__).resolve().parents[2]


def test_alembic_has_single_head() -> None:
    script = ScriptDirectory.from_config(Config(str(REPO_ROOT / "alembic.ini")))
    heads = script.get_heads()
    assert len(heads) == 1, (
        "Multiple alembic heads break `alembic upgrade head`; "
        f"add a merge migration. Heads: {sorted(heads)}"
    )
