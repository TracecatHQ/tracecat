from collections import defaultdict
from pathlib import Path

import pytest

from tracecat.registry.actions.models import (
    RegistryActionValidationErrorInfo,
    TemplateAction,
)
from tracecat.registry.actions.service import validate_action_template
from tracecat.registry.constants import DEFAULT_REGISTRY_ORIGIN
from tracecat.registry.repository import Repository


@pytest.mark.anyio
async def test_base_registry_validate_template_actions():
    origin = DEFAULT_REGISTRY_ORIGIN
    repo = Repository(origin=origin)
    await repo.load_from_origin()
    val_errs: dict[str, list[RegistryActionValidationErrorInfo]] = defaultdict(list)
    for action_name in sorted(repo.store.keys()):
        action = repo.store[action_name]
        if not action.is_template:
            continue
        if errs := await validate_action_template(
            action,
            repo,
            check_db=False,
        ):
            val_errs[action.action].extend(errs)
    if val_errs:
        import io

        from rich.console import Console
        from rich.table import Table

        file = io.StringIO()
        console = Console(file=file)

        # Show this in a table
        table = Table(title="Validation Errors", show_lines=True)
        table.add_column("Action", no_wrap=False, overflow="fold")
        table.add_column("Details", no_wrap=False, overflow="fold")
        table.add_column(
            "Location [dim](Details)[/dim]", no_wrap=False, overflow="fold"
        )
        for action, errs in val_errs.items():
            for err in errs:
                table.add_row(
                    action,
                    "\n".join(err.details),
                    f"{err.loc_primary} [dim]({err.loc_secondary})[/dim]"
                    if err.loc_secondary
                    else err.loc_primary,
                )

        # Render table to string
        console.print(table)
        error_output = file.getvalue()
        raise AssertionError(error_output)
    assert len(val_errs) == 0


@pytest.mark.anyio
@pytest.mark.parametrize(
    "file_path",
    Path("registry/tracecat_registry/templates").rglob("*.yml"),
    ids=lambda path: str(path.parts[-2:]),
)
async def test_template_action_validation(file_path):
    # Initialize the repository
    repo = Repository()
    repo.init(include_base=False, include_templates=True)

    # Test parsing
    action = TemplateAction.from_yaml(file_path)
    assert action.type == "action"
    assert action.definition

    # Test registration
    repo.register_template_action(action)
    assert action.definition.action in repo
