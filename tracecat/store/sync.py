"""Workflow synchronization functionality for Tracecat."""

from __future__ import annotations

from collections.abc import Awaitable, Callable

import yaml

from tracecat.dsl.common import DSLInput
from tracecat.identifiers import WorkspaceID
from tracecat.logger import logger
from tracecat.store.core import WorkflowSource
from tracecat.types.auth import Role
from tracecat.workflow.management.definitions import WorkflowDefinitionsService

# Type alias for YAML fetching function
FetchYaml = Callable[[str, str], Awaitable[str]]


async def upsert_workflow_definitions(
    sources: list[WorkflowSource],
    *,
    fetch_yaml: FetchYaml,
    commit_sha: str,
    workspace_id: WorkspaceID,
    repo_url: str | None = None,
) -> None:
    """Upsert workflow definitions from external sources.

    For each workflow source, fetches the YAML content, parses it to DSL format,
    and upserts it into the WorkflowDefinition table with Git metadata.

    Args:
        sources: List of workflow sources to process.
        fetch_yaml: Function to fetch YAML content given path and SHA.
        commit_sha: Git commit SHA for this sync operation.
        workspace_id: Workspace ID for the definitions.

    Raises:
        Exception: If YAML parsing or database operations fail.
    """
    logger.info(
        "Starting workflow definitions upsert",
        source_count=len(sources),
        commit_sha=commit_sha,
        workspace_id=workspace_id,
    )

    # Create a temporary role for the service
    role = Role(
        type="service",
        service_id="tracecat-service",
        workspace_id=workspace_id,
    )

    async with WorkflowDefinitionsService.with_session(role=role) as service:
        for source in sources:
            try:
                logger.debug(
                    "Processing workflow source",
                    path=source.path,
                    workflow_id=source.workflow_id,
                    sha=source.sha,
                )

                # Fetch YAML content
                yaml_content = await fetch_yaml(source.path, source.sha)

                # Parse YAML to DSL
                workflow_data = yaml.safe_load(yaml_content)
                dsl = DSLInput(**workflow_data)

                logger.debug(
                    "Parsed workflow DSL",
                    title=dsl.title,
                    workflow_id=source.workflow_id,
                )

                # workflow_id is already a WorkflowID instance
                workflow_id = source.workflow_id

                # Create workflow definition with Git metadata
                # Note: We extend the base create method to include Git metadata
                # The exact implementation depends on how WorkflowDefinition schema supports metadata
                defn = await service.create_workflow_definition(
                    workflow_id=workflow_id,
                    dsl=dsl,
                    commit=False,  # We'll commit in batch
                )

                # Add Git metadata to the definition
                # Set git metadata fields on the definition
                defn.origin = "git"
                defn.repo_path = source.path
                defn.commit_sha = commit_sha
                if repo_url is not None:
                    defn.repo_url = repo_url

                logger.info(
                    "Created workflow definition",
                    workflow_id=source.workflow_id,
                    version=defn.version,
                    path=source.path,
                )

            except Exception as e:
                logger.error(
                    "Failed to process workflow source",
                    path=source.path,
                    workflow_id=source.workflow_id,
                    error=str(e),
                )
                raise

        # Commit all changes at once
        await service.session.commit()

        logger.info(
            "Successfully upserted workflow definitions",
            source_count=len(sources),
            commit_sha=commit_sha,
        )
