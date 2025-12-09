"""
CLI script for loading a registry repository and serializing actions to JSON.

This script is designed to run in a subprocess to isolate the potentially
disruptive operations (uv install, importlib.reload, module loading) from
the main API process.

Usage:
    python -m tracecat.registry.sync --origin <origin> [--commit-sha <sha>]

The script outputs a JSON object to stdout containing:
    - actions: List of serialized RegistryActionCreate objects
    - commit_sha: The resolved commit SHA (or null for local/builtin repos)
    - validation_errors: Any validation errors encountered (if validation is enabled)

All logging is sent to stderr to avoid polluting the JSON output.
"""

from __future__ import annotations

import argparse
import asyncio
import json
import sys
from collections import defaultdict
from uuid import UUID

from pydantic import UUID4

from tracecat.auth.types import Role
from tracecat.contexts import ctx_role
from tracecat.logger import logger
from tracecat.registry.actions.enums import TemplateActionValidationErrorType
from tracecat.registry.actions.schemas import (
    RegistryActionCreate,
    RegistryActionValidationErrorInfo,
)
from tracecat.registry.actions.service import validate_action_template
from tracecat.registry.repository import Repository
from tracecat.registry.sync.schemas import (
    SyncResultError,
    SyncResultSuccess,
)
from tracecat.settings.service import get_setting_cached


async def load_and_serialize_actions(
    origin: str,
    repository_id: UUID4,
    commit_sha: str | None = None,
    validate: bool = False,
) -> SyncResultSuccess:
    """Load a repository and serialize its actions to a typed result.

    Args:
        origin: The repository origin (e.g., "tracecat_registry", "local", or a git URL).
        repository_id: The UUID of the repository in the database.
        commit_sha: Optional commit SHA to checkout (for remote repos).
        validate: Whether to validate template actions.

    Returns:
        SyncResultSuccess containing actions, commit_sha, and validation_errors.
    """
    # Set up service role for the subprocess
    # Use "tracecat-service" as the service ID (valid InternalServiceID)
    role = Role(type="service", service_id="tracecat-service")
    ctx_role.set(role)

    # Load the repository (this triggers uv install / reload)
    logger.info("Loading repository", origin=origin, commit_sha=commit_sha)
    repo = Repository(origin=origin, role=role)
    resolved_commit_sha = await repo.load_from_origin(commit_sha=commit_sha)
    logger.info(
        "Repository loaded",
        origin=origin,
        resolved_commit_sha=resolved_commit_sha,
        num_actions=len(repo.store),
    )

    # Validate template actions if requested
    validation_errors: dict[str, list[RegistryActionValidationErrorInfo]] = {}
    if validate:
        logger.info("Validating template actions")
        val_errs: dict[str, list[RegistryActionValidationErrorInfo]] = defaultdict(list)
        for action in repo.store.values():
            if not action.is_template:
                continue
            if errs := await validate_action_template(action, repo, check_db=False):
                val_errs[action.action].extend(errs)
        # Keep typed validation errors
        validation_errors = dict(val_errs)
        if validation_errors:
            logger.warning(
                "Validation errors found",
                num_errors=sum(len(v) for v in validation_errors.values()),
            )

    # Serialize actions to RegistryActionCreate DTOs
    serialized_actions: list[RegistryActionCreate] = []
    for bound_action in repo.store.values():
        try:
            create_dto = RegistryActionCreate.from_bound(bound_action, repository_id)
            serialized_actions.append(create_dto)
        except Exception as e:
            logger.error(
                "Failed to serialize action",
                action=bound_action.action,
                error=str(e),
            )
            # Add to validation errors as a serialization error
            if bound_action.action not in validation_errors:
                validation_errors[bound_action.action] = []
            validation_errors[bound_action.action].append(
                RegistryActionValidationErrorInfo(
                    type=TemplateActionValidationErrorType.SERIALIZATION_ERROR,
                    details=[str(e)],
                    is_template=bound_action.is_template,
                    loc_primary=bound_action.action,
                    loc_secondary=None,
                )
            )

    logger.info(
        "Serialization complete",
        num_actions=len(serialized_actions),
        num_errors=len(validation_errors),
    )

    return SyncResultSuccess(
        actions=serialized_actions,
        commit_sha=resolved_commit_sha,
        validation_errors=validation_errors,
    )


async def main() -> int:
    """Main entry point for the CLI script."""
    parser = argparse.ArgumentParser(
        description="Load a registry repository and serialize actions to JSON."
    )
    parser.add_argument(
        "--origin",
        required=True,
        help="The repository origin (e.g., 'tracecat_registry', 'local', or a git URL).",
    )
    parser.add_argument(
        "--repository-id",
        required=True,
        help="The UUID of the repository in the database.",
    )
    parser.add_argument(
        "--commit-sha",
        default=None,
        help="Optional commit SHA to checkout (for remote repos).",
    )
    parser.add_argument(
        "--validate",
        action="store_true",
        help="Whether to validate template actions.",
    )

    args = parser.parse_args()

    try:
        # Parse repository ID as UUID
        repository_id = UUID(args.repository_id)
    except ValueError as e:
        logger.error("Invalid repository ID", error=str(e))
        print(
            json.dumps({"error": f"Invalid repository ID: {e}"}),
            file=sys.stdout,
        )
        return 1

    try:
        # Check if validation is enabled via settings (can override --validate flag)
        should_validate: bool = bool(
            args.validate
            or await get_setting_cached(
                "app_registry_validation_enabled",
                default=False,
            )
        )

        result = await load_and_serialize_actions(
            origin=args.origin,
            repository_id=repository_id,
            commit_sha=args.commit_sha,
            validate=should_validate,
        )

        # Output result as JSON to stdout using Pydantic serialization
        print(result.model_dump_json(), file=sys.stdout)
        return 0

    except Exception as e:
        logger.exception("Failed to load repository", error=str(e))
        # Output error as typed JSON
        error_result = SyncResultError(error=str(e))
        print(error_result.model_dump_json(), file=sys.stdout)
        return 1


if __name__ == "__main__":
    exit_code = asyncio.run(main())
    sys.exit(exit_code)
