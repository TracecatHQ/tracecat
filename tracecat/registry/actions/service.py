from __future__ import annotations

import uuid
from collections import defaultdict
from collections.abc import Sequence
from dataclasses import dataclass
from typing import TYPE_CHECKING, TypedDict
from typing import cast as typing_cast

from pydantic import ValidationError
from pydantic_core import ErrorDetails, to_jsonable_python
from sqlalchemy import (
    Boolean,
    cast,
    func,
    literal,
    or_,
    select,
    text,
    tuple_,
    union_all,
)
from tracecat_registry import (
    RegistryOAuthSecret,
    RegistrySecretType,
    RegistrySecretTypeValidator,
)

from tracecat.db.models import (
    PlatformRegistryAction,
    PlatformRegistryIndex,
    PlatformRegistryRepository,
    PlatformRegistryVersion,
    RegistryAction,
    RegistryIndex,
    RegistryRepository,
    RegistryVersion,
)
from tracecat.exceptions import (
    RegistryActionValidationError,
    RegistryError,
    RegistryValidationError,
)
from tracecat.expressions.eval import extract_expressions
from tracecat.expressions.expectations import create_expectation_model
from tracecat.expressions.validator.validator import (
    TemplateActionExprValidator,
    TemplateActionValidationContext,
)
from tracecat.logger import logger
from tracecat.registry.actions.bound import BoundRegistryAction
from tracecat.registry.actions.enums import (
    TemplateActionValidationErrorType,
)
from tracecat.registry.actions.schemas import (
    AnnotatedRegistryActionImpl,
    RegistryActionCreate,
    RegistryActionImplValidator,
    RegistryActionInterface,
    RegistryActionOptions,
    RegistryActionRead,
    RegistryActionType,
    RegistryActionUpdate,
    RegistryActionValidationErrorInfo,
    TemplateAction,
)
from tracecat.registry.loaders import LoaderMode, get_bound_action_impl
from tracecat.registry.repository import Repository
from tracecat.registry.sync.service import RegistrySyncService
from tracecat.registry.sync.subprocess import fetch_actions_from_subprocess
from tracecat.registry.versions.schemas import RegistryVersionManifest
from tracecat.secrets.schemas import SecretDefinition
from tracecat.service import BaseService
from tracecat.settings.service import get_setting_cached

if TYPE_CHECKING:
    from tracecat.ssh import SshEnv


class SecretAggregate(TypedDict):
    keys: set[str]
    optional_keys: set[str]
    optional: bool
    actions: set[str]


@dataclass(slots=True)
class IndexEntry:
    """Simple data holder that mimics BaseRegistryIndex for list_actions_from_index results.

    Used to convert raw UNION ALL query results into objects compatible with
    RegistryActionReadMinimal.from_index() and RegistryActionRead.from_index_and_manifest().

    Implements RegistryIndexLike protocol.
    """

    id: uuid.UUID
    namespace: str
    name: str
    action_type: str
    description: str
    default_title: str | None
    display_group: str | None
    options: dict
    doc_url: str | None = None
    author: str | None = None
    deprecated: str | None = None


@dataclass(slots=True)
class IndexedActionResult:
    """Result from index/manifest lookup operations.

    Combines index entry metadata with the full manifest for action resolution.
    """

    index_entry: IndexEntry
    manifest: RegistryVersionManifest
    origin: str
    repository_id: uuid.UUID


class RegistryActionsService(BaseService):
    """Registry actions service."""

    service_name = "registry_actions"

    async def list_actions(
        self,
        *,
        namespace: str | None = None,
        include_marked: bool = False,
        include_keys: set[str] | None = None,
    ) -> Sequence[RegistryAction]:
        statement = select(RegistryAction).where(
            RegistryAction.organization_id == self.organization_id
        )

        if not include_marked:
            statement = statement.where(
                cast(RegistryAction.options["include_in_schema"].astext, Boolean)  # noqa: E712
                == True  # noqa: E712
            )

        if namespace:
            statement = statement.where(
                RegistryAction.namespace.startswith(namespace),
            )

        if include_keys:
            statement = statement.where(
                or_(
                    func.concat(RegistryAction.namespace, ".", RegistryAction.name).in_(
                        include_keys
                    )
                )
            )

        result = await self.session.execute(statement)
        return result.scalars().all()

    async def list_actions_from_index(
        self,
        *,
        namespace: str | None = None,
        include_marked: bool = False,
        include_keys: set[str] | None = None,
    ) -> list[tuple[IndexEntry, str]]:
        """List actions from registry index for current versions.

        Returns tuples of (index_entry, origin) from both org-scoped and platform registries.
        Uses a single database call with UNION ALL for efficiency.
        """
        # Org-scoped registry query
        org_statement = (
            select(
                RegistryIndex.id,
                RegistryIndex.namespace,
                RegistryIndex.name,
                RegistryIndex.action_type,
                RegistryIndex.description,
                RegistryIndex.default_title,
                RegistryIndex.display_group,
                RegistryIndex.options,
                RegistryRepository.origin,
                literal("org").label("source"),
            )
            .join(
                RegistryVersion,
                RegistryIndex.registry_version_id == RegistryVersion.id,
            )
            .join(
                RegistryRepository,
                RegistryVersion.repository_id == RegistryRepository.id,
            )
            .where(
                RegistryRepository.organization_id == self.organization_id,
                RegistryRepository.current_version_id == RegistryVersion.id,
            )
        )

        # Platform registry query (same columns for UNION ALL compatibility)
        platform_statement = (
            select(
                PlatformRegistryIndex.id,
                PlatformRegistryIndex.namespace,
                PlatformRegistryIndex.name,
                PlatformRegistryIndex.action_type,
                PlatformRegistryIndex.description,
                PlatformRegistryIndex.default_title,
                PlatformRegistryIndex.display_group,
                PlatformRegistryIndex.options,
                PlatformRegistryRepository.origin,
                literal("platform").label("source"),
            )
            .join(
                PlatformRegistryVersion,
                PlatformRegistryIndex.registry_version_id == PlatformRegistryVersion.id,
            )
            .join(
                PlatformRegistryRepository,
                PlatformRegistryVersion.repository_id == PlatformRegistryRepository.id,
            )
            .where(
                PlatformRegistryRepository.current_version_id
                == PlatformRegistryVersion.id,
            )
        )

        # Apply filters to both queries
        if not include_marked:
            org_statement = org_statement.where(
                cast(RegistryIndex.options["include_in_schema"].astext, Boolean).is_(
                    True
                )
            )
            platform_statement = platform_statement.where(
                cast(
                    PlatformRegistryIndex.options["include_in_schema"].astext, Boolean
                ).is_(True)
            )
        if namespace:
            org_statement = org_statement.where(
                RegistryIndex.namespace.startswith(namespace)
            )
            platform_statement = platform_statement.where(
                PlatformRegistryIndex.namespace.startswith(namespace)
            )
        if include_keys:
            org_statement = org_statement.where(
                func.concat(RegistryIndex.namespace, ".", RegistryIndex.name).in_(
                    include_keys
                )
            )
            platform_statement = platform_statement.where(
                func.concat(
                    PlatformRegistryIndex.namespace, ".", PlatformRegistryIndex.name
                ).in_(include_keys)
            )

        # Combine with UNION ALL for a single database call
        combined = union_all(org_statement, platform_statement)
        result = await self.session.execute(combined)
        rows = result.tuples().all()

        # Convert raw tuples to index-like objects for compatibility with from_index()
        entries: list[tuple[IndexEntry, str]] = []
        for (
            id_,
            namespace_,
            name,
            action_type,
            description,
            default_title,
            display_group,
            options,
            origin,
            _,  # source indicator (org/platform), not needed in result
        ) in rows:
            entry = IndexEntry(
                id=id_,
                namespace=namespace_,
                name=name,
                action_type=action_type,
                description=description,
                default_title=default_title,
                display_group=display_group,
                options=options,
            )
            entries.append((entry, origin))
        return entries

    async def list_actions_from_index_by_repository(
        self,
        repository_id: uuid.UUID,
    ) -> list[RegistryActionRead]:
        """List full action details from registry index for a specific repository.

        Queries both org-scoped and platform registry tables using UNION ALL.
        Since repository_id is unique, only one table will have matching results.

        Returns list of RegistryActionRead objects with implementation from manifest.
        """
        # Org-scoped query - filter by org_id for security
        org_statement = (
            select(
                RegistryIndex.id,
                RegistryIndex.namespace,
                RegistryIndex.name,
                RegistryIndex.action_type,
                RegistryIndex.description,
                RegistryIndex.default_title,
                RegistryIndex.display_group,
                RegistryIndex.options,
                RegistryIndex.doc_url,
                RegistryIndex.author,
                RegistryIndex.deprecated,
                RegistryVersion.manifest,
                RegistryRepository.origin,
                RegistryRepository.id.label("repo_id"),
            )
            .join(
                RegistryVersion,
                RegistryIndex.registry_version_id == RegistryVersion.id,
            )
            .join(
                RegistryRepository,
                RegistryVersion.repository_id == RegistryRepository.id,
            )
            .where(
                RegistryRepository.id == repository_id,
                RegistryRepository.current_version_id == RegistryVersion.id,
                RegistryRepository.organization_id == self.organization_id,
            )
        )

        # Platform query - no org_id filter (shared across all orgs)
        platform_statement = (
            select(
                PlatformRegistryIndex.id,
                PlatformRegistryIndex.namespace,
                PlatformRegistryIndex.name,
                PlatformRegistryIndex.action_type,
                PlatformRegistryIndex.description,
                PlatformRegistryIndex.default_title,
                PlatformRegistryIndex.display_group,
                PlatformRegistryIndex.options,
                PlatformRegistryIndex.doc_url,
                PlatformRegistryIndex.author,
                PlatformRegistryIndex.deprecated,
                PlatformRegistryVersion.manifest,
                PlatformRegistryRepository.origin,
                PlatformRegistryRepository.id.label("repo_id"),
            )
            .join(
                PlatformRegistryVersion,
                PlatformRegistryIndex.registry_version_id == PlatformRegistryVersion.id,
            )
            .join(
                PlatformRegistryRepository,
                PlatformRegistryVersion.repository_id == PlatformRegistryRepository.id,
            )
            .where(
                PlatformRegistryRepository.id == repository_id,
                PlatformRegistryRepository.current_version_id
                == PlatformRegistryVersion.id,
            )
        )

        # Single query combining both table sets
        combined = union_all(org_statement, platform_statement)
        result = await self.session.execute(combined)
        rows = result.tuples().all()

        actions: list[RegistryActionRead] = []
        for (
            id_,
            namespace,
            name,
            action_type,
            description,
            default_title,
            display_group,
            options,
            doc_url,
            author,
            deprecated,
            manifest_data,
            origin,
            repo_id,
        ) in rows:
            manifest = RegistryVersionManifest.model_validate(manifest_data)
            action_name = f"{namespace}.{name}"
            manifest_action = manifest.actions.get(action_name)
            if manifest_action:
                # Create index-like object for from_index_and_manifest
                index_entry = IndexEntry(
                    id=id_,
                    namespace=namespace,
                    name=name,
                    action_type=action_type,
                    description=description,
                    default_title=default_title,
                    display_group=display_group,
                    options=options or {},
                    doc_url=doc_url,
                    author=author,
                    deprecated=deprecated,
                )
                actions.append(
                    RegistryActionRead.from_index_and_manifest(
                        index_entry,
                        manifest_action,
                        origin,
                        repo_id,
                    )
                )
        return actions

    async def get_action_from_index(
        self,
        action_name: str,
    ) -> IndexedActionResult | None:
        """Get action from index with manifest from the version.

        Searches both org-scoped and platform registries using a single UNION ALL query.
        Org-scoped results take precedence over platform results.

        Args:
            action_name: Full action name (e.g., "core.http_request")

        Returns:
            IndexedActionResult or None if not found.
        """
        try:
            namespace, name = action_name.rsplit(".", 1)
        except ValueError:
            return None

        # Org-scoped query
        org_statement = (
            select(
                RegistryIndex.id,
                RegistryIndex.namespace,
                RegistryIndex.name,
                RegistryIndex.action_type,
                RegistryIndex.description,
                RegistryIndex.default_title,
                RegistryIndex.display_group,
                RegistryIndex.options,
                RegistryIndex.doc_url,
                RegistryIndex.author,
                RegistryIndex.deprecated,
                RegistryVersion.manifest,
                RegistryRepository.origin,
                RegistryRepository.id.label("repo_id"),
                literal("org").label("source"),
            )
            .join(
                RegistryVersion,
                RegistryIndex.registry_version_id == RegistryVersion.id,
            )
            .join(
                RegistryRepository,
                RegistryVersion.repository_id == RegistryRepository.id,
            )
            .where(
                RegistryRepository.organization_id == self.organization_id,
                RegistryRepository.current_version_id == RegistryVersion.id,
                RegistryIndex.namespace == namespace,
                RegistryIndex.name == name,
            )
        )

        # Platform query
        platform_statement = (
            select(
                PlatformRegistryIndex.id,
                PlatformRegistryIndex.namespace,
                PlatformRegistryIndex.name,
                PlatformRegistryIndex.action_type,
                PlatformRegistryIndex.description,
                PlatformRegistryIndex.default_title,
                PlatformRegistryIndex.display_group,
                PlatformRegistryIndex.options,
                PlatformRegistryIndex.doc_url,
                PlatformRegistryIndex.author,
                PlatformRegistryIndex.deprecated,
                PlatformRegistryVersion.manifest,
                PlatformRegistryRepository.origin,
                PlatformRegistryRepository.id.label("repo_id"),
                literal("platform").label("source"),
            )
            .join(
                PlatformRegistryVersion,
                PlatformRegistryIndex.registry_version_id == PlatformRegistryVersion.id,
            )
            .join(
                PlatformRegistryRepository,
                PlatformRegistryVersion.repository_id == PlatformRegistryRepository.id,
            )
            .where(
                PlatformRegistryRepository.current_version_id
                == PlatformRegistryVersion.id,
                PlatformRegistryIndex.namespace == namespace,
                PlatformRegistryIndex.name == name,
            )
        )

        # Single query with UNION ALL, ordered so org results come first
        combined = union_all(org_statement, platform_statement).order_by(
            text("source")  # "org" < "platform" alphabetically
        )
        result = await self.session.execute(combined)
        first_row = result.tuples().first()

        if not first_row:
            return None

        (
            id_,
            namespace_,
            name_,
            action_type,
            description,
            default_title,
            display_group,
            options,
            doc_url,
            author,
            deprecated,
            manifest_data,
            origin,
            repo_id,
            _,  # source indicator
        ) = first_row

        manifest = RegistryVersionManifest.model_validate(manifest_data)
        return IndexedActionResult(
            index_entry=IndexEntry(
                id=id_,
                namespace=namespace_,
                name=name_,
                action_type=action_type,
                description=description,
                default_title=default_title,
                display_group=display_group,
                options=options or {},
                doc_url=doc_url,
                author=author,
                deprecated=deprecated,
            ),
            manifest=manifest,
            origin=origin,
            repository_id=repo_id,
        )

    async def get_actions_from_index(
        self,
        action_names: list[str],
    ) -> dict[str, IndexedActionResult]:
        """Batch fetch actions from index + manifest.

        Searches both org-scoped and platform registries for actions.

        Args:
            action_names: List of full action names (e.g., ["core.http_request", "tools.slack.post_message"])

        Returns:
            Dict mapping action_name -> IndexedActionResult.
            Actions not found are omitted from the result.
        """
        if not action_names:
            return {}

        # Parse action names into (namespace, name) pairs for query
        action_parts: list[tuple[str, str]] = []
        for action_name in action_names:
            try:
                namespace, name = action_name.rsplit(".", 1)
                action_parts.append((namespace, name))
            except ValueError:
                self.logger.warning(
                    "Invalid action name format, skipping",
                    action_name=action_name,
                )
                continue

        if not action_parts:
            return {}

        # Build condition for matching any of the (namespace, name) pairs
        org_conditions = tuple_(RegistryIndex.namespace, RegistryIndex.name).in_(
            action_parts
        )
        platform_conditions = tuple_(
            PlatformRegistryIndex.namespace, PlatformRegistryIndex.name
        ).in_(action_parts)

        # Org-scoped query
        org_statement = (
            select(
                RegistryIndex.id,
                RegistryIndex.namespace,
                RegistryIndex.name,
                RegistryIndex.action_type,
                RegistryIndex.description,
                RegistryIndex.default_title,
                RegistryIndex.display_group,
                RegistryIndex.options,
                RegistryIndex.doc_url,
                RegistryIndex.author,
                RegistryIndex.deprecated,
                RegistryVersion.manifest,
                RegistryRepository.origin,
                RegistryRepository.id.label("repo_id"),
            )
            .join(
                RegistryVersion,
                RegistryIndex.registry_version_id == RegistryVersion.id,
            )
            .join(
                RegistryRepository,
                RegistryVersion.repository_id == RegistryRepository.id,
            )
            .where(
                RegistryRepository.organization_id == self.organization_id,
                RegistryRepository.current_version_id == RegistryVersion.id,
                org_conditions,
            )
        )

        # Platform query
        platform_statement = (
            select(
                PlatformRegistryIndex.id,
                PlatformRegistryIndex.namespace,
                PlatformRegistryIndex.name,
                PlatformRegistryIndex.action_type,
                PlatformRegistryIndex.description,
                PlatformRegistryIndex.default_title,
                PlatformRegistryIndex.display_group,
                PlatformRegistryIndex.options,
                PlatformRegistryIndex.doc_url,
                PlatformRegistryIndex.author,
                PlatformRegistryIndex.deprecated,
                PlatformRegistryVersion.manifest,
                PlatformRegistryRepository.origin,
                PlatformRegistryRepository.id.label("repo_id"),
            )
            .join(
                PlatformRegistryVersion,
                PlatformRegistryIndex.registry_version_id == PlatformRegistryVersion.id,
            )
            .join(
                PlatformRegistryRepository,
                PlatformRegistryVersion.repository_id == PlatformRegistryRepository.id,
            )
            .where(
                PlatformRegistryRepository.current_version_id
                == PlatformRegistryVersion.id,
                platform_conditions,
            )
        )

        # Combine with UNION ALL
        combined = union_all(org_statement, platform_statement)
        result = await self.session.execute(combined)
        rows = result.tuples().all()

        actions: dict[str, IndexedActionResult] = {}
        for (
            id_,
            namespace,
            name,
            action_type,
            description,
            default_title,
            display_group,
            options,
            doc_url,
            author,
            deprecated,
            manifest_data,
            origin,
            repo_id,
        ) in rows:
            action_name = f"{namespace}.{name}"
            # Skip if already found (org-scoped takes precedence)
            if action_name in actions:
                continue

            index_entry = IndexEntry(
                id=id_,
                namespace=namespace,
                name=name,
                action_type=action_type,
                description=description,
                default_title=default_title,
                display_group=display_group,
                options=options or {},
                doc_url=doc_url,
                author=author,
                deprecated=deprecated,
            )
            manifest = RegistryVersionManifest.model_validate(manifest_data)
            actions[action_name] = IndexedActionResult(
                index_entry=index_entry,
                manifest=manifest,
                origin=origin,
                repository_id=repo_id,
            )

        return actions

    async def search_actions_from_index(
        self,
        query: str,
        *,
        limit: int | None = None,
    ) -> list[tuple[IndexEntry, str]]:
        """Search actions by name or description using ilike.

        Searches both org-scoped and platform registries.

        Args:
            query: Search query string
            limit: Maximum number of results to return (None for no limit)

        Returns:
            List of (index_entry, origin) tuples matching the search.
        """
        if not query:
            return []

        search_pattern = f"%{query}%"

        # Org-scoped query
        org_statement = (
            select(
                RegistryIndex.id,
                RegistryIndex.namespace,
                RegistryIndex.name,
                RegistryIndex.action_type,
                RegistryIndex.description,
                RegistryIndex.default_title,
                RegistryIndex.display_group,
                RegistryIndex.options,
                RegistryIndex.doc_url,
                RegistryIndex.author,
                RegistryIndex.deprecated,
                RegistryRepository.origin,
                literal("org").label("source"),
            )
            .join(
                RegistryVersion,
                RegistryIndex.registry_version_id == RegistryVersion.id,
            )
            .join(
                RegistryRepository,
                RegistryVersion.repository_id == RegistryRepository.id,
            )
            .where(
                RegistryRepository.organization_id == self.organization_id,
                RegistryRepository.current_version_id == RegistryVersion.id,
                or_(
                    func.concat(RegistryIndex.namespace, ".", RegistryIndex.name).ilike(
                        search_pattern
                    ),
                    RegistryIndex.description.ilike(search_pattern),
                ),
            )
        )

        # Platform query
        platform_statement = (
            select(
                PlatformRegistryIndex.id,
                PlatformRegistryIndex.namespace,
                PlatformRegistryIndex.name,
                PlatformRegistryIndex.action_type,
                PlatformRegistryIndex.description,
                PlatformRegistryIndex.default_title,
                PlatformRegistryIndex.display_group,
                PlatformRegistryIndex.options,
                PlatformRegistryIndex.doc_url,
                PlatformRegistryIndex.author,
                PlatformRegistryIndex.deprecated,
                PlatformRegistryRepository.origin,
                literal("platform").label("source"),
            )
            .join(
                PlatformRegistryVersion,
                PlatformRegistryIndex.registry_version_id == PlatformRegistryVersion.id,
            )
            .join(
                PlatformRegistryRepository,
                PlatformRegistryVersion.repository_id == PlatformRegistryRepository.id,
            )
            .where(
                PlatformRegistryRepository.current_version_id
                == PlatformRegistryVersion.id,
                or_(
                    func.concat(
                        PlatformRegistryIndex.namespace, ".", PlatformRegistryIndex.name
                    ).ilike(search_pattern),
                    PlatformRegistryIndex.description.ilike(search_pattern),
                ),
            )
        )

        # Combine with UNION ALL and optionally apply limit
        combined = union_all(org_statement, platform_statement)
        if limit is not None:
            combined = combined.limit(limit)
        result = await self.session.execute(combined)
        rows = result.tuples().all()

        entries: list[tuple[IndexEntry, str]] = []
        seen_actions: set[str] = set()

        for (
            id_,
            namespace,
            name,
            action_type,
            description,
            default_title,
            display_group,
            options,
            doc_url,
            author,
            deprecated,
            origin,
            _,  # source indicator
        ) in rows:
            action_name = f"{namespace}.{name}"
            # Skip duplicates (org-scoped takes precedence in union order)
            if action_name in seen_actions:
                continue
            seen_actions.add(action_name)

            entry = IndexEntry(
                id=id_,
                namespace=namespace,
                name=name,
                action_type=action_type,
                description=description,
                default_title=default_title,
                display_group=display_group,
                options=options or {},
                doc_url=doc_url,
                author=author,
                deprecated=deprecated,
            )
            entries.append((entry, origin))

        return entries

    async def get_aggregated_secrets(self) -> list[SecretDefinition]:
        """Aggregate secrets from all actions in both org and platform registries.

        Queries manifests from current versions in both registry tables using
        UNION ALL for efficiency.
        """
        # Org-scoped query - get manifests from current versions
        org_statement = (
            select(RegistryVersion.manifest)
            .join(
                RegistryRepository,
                RegistryVersion.repository_id == RegistryRepository.id,
            )
            .where(
                RegistryRepository.organization_id == self.organization_id,
                RegistryRepository.current_version_id == RegistryVersion.id,
            )
        )

        # Platform query - get manifests from current versions
        platform_statement = (
            select(PlatformRegistryVersion.manifest).join(
                PlatformRegistryRepository,
                PlatformRegistryVersion.repository_id == PlatformRegistryRepository.id,
            )
        ).where(
            PlatformRegistryRepository.current_version_id == PlatformRegistryVersion.id,
        )

        # Combine with UNION ALL
        combined = union_all(org_statement, platform_statement)
        result = await self.session.execute(combined)
        manifest_rows = result.scalars().all()

        aggregated: dict[str, SecretAggregate] = {}
        seen_actions: set[str] = set()

        for manifest_data in manifest_rows:
            manifest = RegistryVersionManifest.model_validate(manifest_data)
            for action_name, manifest_action in manifest.actions.items():
                # Skip if already processed (org takes precedence)
                if action_name in seen_actions:
                    continue
                seen_actions.add(action_name)

                if not manifest_action.secrets:
                    continue

                for secret in manifest_action.secrets:
                    if isinstance(secret, RegistryOAuthSecret) or secret.name.endswith(
                        "_oauth"
                    ):
                        continue

                    entry = aggregated.setdefault(
                        secret.name,
                        {
                            "keys": set(),
                            "optional_keys": set(),
                            "optional": False,
                            "actions": set(),
                        },
                    )
                    if secret.keys:
                        entry["keys"].update(secret.keys)
                    if secret.optional_keys:
                        entry["optional_keys"].update(secret.optional_keys)
                    entry["optional"] = entry["optional"] or secret.optional
                    entry["actions"].add(action_name)

        definitions: list[SecretDefinition] = []
        for name, data in aggregated.items():
            required_keys = sorted(data["keys"])
            optional_keys = sorted(set(data["optional_keys"]) - set(required_keys))
            actions = sorted(data["actions"])
            definitions.append(
                SecretDefinition(
                    name=name,
                    keys=required_keys,
                    optional_keys=optional_keys or None,
                    optional=data["optional"],
                    actions=actions,
                    action_count=len(actions),
                )
            )

        return sorted(
            definitions,
            key=lambda definition: (-definition.action_count, definition.name),
        )

    async def get_action(self, action_name: str) -> RegistryAction:
        """Get action by name from RegistryAction table."""
        try:
            namespace, name = action_name.rsplit(".", maxsplit=1)
        except ValueError:
            raise RegistryError(
                f"Action {action_name} is not a valid action name",
                detail={"action_name": action_name},
            ) from None

        statement = select(RegistryAction).where(
            RegistryAction.organization_id == self.organization_id,
            RegistryAction.namespace == namespace,
            RegistryAction.name == name,
        )
        result = await self.session.execute(statement)
        action = result.scalars().one_or_none()
        if not action:
            raise RegistryError(f"Action {namespace}.{name} not found in the registry")
        return action

    async def get_action_by_impl(self, module: str, name: str) -> RegistryAction:
        """Get an action by its implementation module and function name.

        This is used when we have the action_impl metadata (module path and function name)
        but need to load the registry action for execution.

        Args:
            module: The module path (e.g., 'tracecat_registry.integrations.core.transform')
            name: The function name (e.g., 'reshape')

        Returns:
            The registry action matching the implementation.

        Raises:
            RegistryError: If no action with matching implementation is found.
        """
        # Query for UDF actions that match the module and function name
        statement = select(RegistryAction).where(
            RegistryAction.organization_id == self.organization_id,
            RegistryAction.implementation["type"].astext == "udf",
            RegistryAction.implementation["module"].astext == module,
            RegistryAction.implementation["name"].astext == name,
        )
        result = await self.session.execute(statement)
        action = result.scalars().first()
        if not action:
            raise RegistryError(
                f"Action with implementation {module}.{name} not found in the registry",
                detail={"module": module, "name": name},
            )
        return action

    async def get_actions(
        self, action_names: list[str]
    ) -> Sequence[RegistryAction | PlatformRegistryAction]:
        """Get actions by name from both org-scoped and platform registries.

        Searches both RegistryAction (org-scoped) and PlatformRegistryAction (platform)
        tables using UNION ALL. Results from both tables are combined.
        """
        # Org-scoped actions
        org_statement = select(RegistryAction).where(
            RegistryAction.organization_id == self.organization_id,
            func.concat(RegistryAction.namespace, ".", RegistryAction.name).in_(
                action_names
            ),
        )

        # Platform actions
        platform_statement = select(PlatformRegistryAction).where(
            func.concat(
                PlatformRegistryAction.namespace, ".", PlatformRegistryAction.name
            ).in_(action_names),
        )

        # Execute both queries and combine results
        org_result = await self.session.execute(org_statement)
        platform_result = await self.session.execute(platform_statement)

        org_actions = list(org_result.scalars().all())
        platform_actions = list(platform_result.scalars().all())

        return org_actions + platform_actions

    async def create_action(
        self,
        params: RegistryActionCreate,
        *,
        commit: bool = True,
    ) -> RegistryAction:
        """
        Create a new registry action.

        Args:
            params (RegistryActionCreate): Parameters for creating the action.

        Returns:
            DBRegistryAction: The created registry action.
        """

        # Interface
        if params.implementation.type == "template":
            interface = _implementation_to_interface(params.implementation)
        else:
            interface = params.interface

        action = RegistryAction(
            organization_id=self.organization_id,
            interface=to_jsonable_python(interface),
            **params.model_dump(exclude={"interface"}),
        )

        self.session.add(action)
        if commit:
            await self.session.commit()
        else:
            await self.session.flush()
        return action

    async def update_action(
        self,
        action: RegistryAction,
        params: RegistryActionUpdate,
        *,
        commit: bool = True,
    ) -> RegistryAction:
        """
        Update an existing registry action.

        Args:
            db_template (DBRegistryAction): The existing registry action to update.
            params (RegistryActionUpdate): Parameters for updating the action.

        Returns:
            DBRegistryAction: The updated registry action.
        """
        set_fields = params.model_dump(exclude_unset=True)
        for key, value in set_fields.items():
            setattr(action, key, value)
        self.session.add(action)
        if commit:
            await self.session.commit()
        else:
            await self.session.flush()
        return action

    async def delete_action(
        self, action: RegistryAction, *, commit: bool = True
    ) -> RegistryAction:
        """
        Delete a registry action.

        Args:
            template (DBRegistryAction): The registry action to delete.

        Returns:
            DBRegistryAction: The deleted registry action.
        """
        await self.session.delete(action)
        if commit:
            await self.session.commit()
        else:
            await self.session.flush()
        return action

    async def sync_actions_from_repository(
        self,
        db_repo: RegistryRepository,
        pull_remote: bool = True,
        target_commit_sha: str | None = None,
        *,
        allow_delete_all: bool = False,
        use_subprocess: bool = True,
    ) -> str | None:
        """Sync actions from a repository.

        By default, this method runs the repository loading in a subprocess to isolate
        the uv install and importlib.reload operations from the main API process.

        Args:
            db_repo: The repository to sync.
            pull_remote: Whether to pull the latest changes from the remote.
            target_commit_sha: Optional commit SHA to sync to.
            allow_delete_all: If True, allow deleting all actions if the list is empty.
            use_subprocess: If True (default), run the sync in a subprocess.
                Set to False for testing purposes when environment can't be
                passed to subprocess (e.g., monkeypatched config).

        To sync actions from the db repositories:
        - Run a subprocess to reimport packages and serialize action metadata
        - Update the DB with the serialized actions
        """
        # Determine which commit SHA to use:
        # 1. If target_commit_sha is provided, use it
        # 2. If pull_remote is False, use the stored commit SHA
        # 3. Otherwise use None (HEAD)
        if target_commit_sha is not None:
            sha = target_commit_sha
        elif not pull_remote:
            sha = db_repo.commit_sha
        else:
            sha = None

        # Check if validation is enabled
        should_validate: bool = (
            await get_setting_cached(
                "app_registry_validation_enabled",
                default=False,
            )
            or False
        )
        self.logger.info("Registry validation enabled", enabled=should_validate)

        if use_subprocess:
            # Run the sync subprocess to load the repository and serialize actions
            # This isolates uv install / importlib.reload from the main process
            sync_result = await fetch_actions_from_subprocess(
                origin=db_repo.origin,
                repository_id=db_repo.id,
                commit_sha=sha,
                validate=should_validate,
            )

            # Check for validation errors
            if sync_result.validation_errors:
                raise RegistryActionValidationError(
                    f"Found {sum(len(v) for v in sync_result.validation_errors.values())} validation error(s)",
                    detail=sync_result.validation_errors,
                )

            actions = sync_result.actions
            commit_sha = sync_result.commit_sha
        else:
            # In-process sync (for testing or when subprocess is not suitable)
            repo = Repository(origin=db_repo.origin, role=self.role)
            commit_sha = await repo.load_from_origin(commit_sha=sha)

            # Validate if enabled
            if should_validate:
                self.logger.info("Validating actions", all_actions=repo.store.keys())
                val_errs: dict[str, list[RegistryActionValidationErrorInfo]] = (
                    defaultdict(list)
                )
                for action in repo.store.values():
                    if not action.is_template:
                        continue
                    if errs := await self.validate_action_template(action, repo):
                        val_errs[action.action].extend(errs)
                if val_errs:
                    raise RegistryActionValidationError(
                        f"Found {sum(len(v) for v in val_errs.values())} validation error(s)",
                        detail=val_errs,
                    )

            # Convert to RegistryActionCreate DTOs
            actions = [
                RegistryActionCreate.from_bound(bound_action, db_repo.id)
                for bound_action in repo.store.values()
            ]

        # Perform DB mutations in a single transaction to avoid partial writes
        if self.session.in_transaction():
            async with self.session.begin_nested():
                await self.upsert_actions_from_list(
                    actions,
                    db_repo,
                    commit=False,
                    allow_delete_all=allow_delete_all,
                )
        else:
            async with self.session.begin():
                await self.upsert_actions_from_list(
                    actions,
                    db_repo,
                    commit=False,
                    allow_delete_all=allow_delete_all,
                )

        return commit_sha

    async def sync_actions_from_repository_v2(
        self,
        db_repo: RegistryRepository,
        *,
        target_version: str | None = None,
        target_commit_sha: str | None = None,
        ssh_env: SshEnv | None = None,
    ) -> tuple[str | None, str | None]:
        """Sync actions from a repository using the v2 versioned flow.

        This creates an immutable RegistryVersion snapshot with:
        - Frozen manifest stored in DB
        - Tarball venv uploaded to S3 (mandatory - sync fails if build fails)
        - RegistryIndex entries for fast lookups

        Note: RegistryAction table is populated by platform registry sync only.
        Use RegistryIndex for UI queries and fetch implementation from manifest.

        Args:
            db_repo: The repository to sync.
            target_version: Version string (auto-generated if not provided).
            target_commit_sha: Optional commit SHA to sync to.
            ssh_env: SSH environment for git operations (required for git+ssh repos).

        Returns:
            Tuple of (commit_sha, version_string)

        Raises:
            TarballBuildError: If tarball building fails (no version is created)
        """
        # Use the v2 sync service
        sync_service = RegistrySyncService(self.session, self.role)

        if self.session.in_transaction():
            async with self.session.begin_nested():
                sync_result = await sync_service.sync_repository_v2(
                    db_repo=db_repo,
                    target_version=target_version,
                    target_commit_sha=target_commit_sha,
                    ssh_env=ssh_env,
                    commit=False,
                )
        else:
            async with self.session.begin():
                sync_result = await sync_service.sync_repository_v2(
                    db_repo=db_repo,
                    target_version=target_version,
                    target_commit_sha=target_commit_sha,
                    ssh_env=ssh_env,
                    commit=False,
                )

        self.logger.info(
            "V2 sync completed",
            version=sync_result.version_string,
            commit_sha=sync_result.commit_sha,
            num_actions=sync_result.num_actions,
        )

        return sync_result.commit_sha, sync_result.version_string

    async def get_action_or_none(self, action_name: str) -> RegistryAction | None:
        """Get an action by name, returning None if it doesn't exist."""
        try:
            return await self.get_action(action_name)
        except RegistryError:
            return None

    async def validate_action_template(
        self, action: BoundRegistryAction, repo: Repository
    ) -> list[RegistryActionValidationErrorInfo]:
        """Validate that a template action is correctly formatted."""
        return await validate_action_template(
            action, repo, check_db=True, ra_service=self
        )

    async def upsert_actions_from_list(
        self,
        actions: list[RegistryActionCreate],
        db_repo: RegistryRepository,
        *,
        commit: bool = True,
        allow_delete_all: bool = False,
    ) -> None:
        """Upsert a list of actions from pre-serialized RegistryActionCreate objects.

        This method is used by the subprocess-based sync flow where actions
        are already serialized to DTOs.

        Args:
            actions: List of RegistryActionCreate DTOs to upsert.
            db_repo: The database repository record.
            commit: Whether to commit after each operation.
            allow_delete_all: If True, allow deleting all actions if the list is empty.
        """
        # Build a map of incoming actions by their full action name
        incoming_actions_map = {
            f"{action.namespace}.{action.name}": action for action in actions
        }

        # Get existing actions from the DB
        await self.session.refresh(db_repo)
        db_actions = db_repo.actions
        db_actions_map = {db_action.action: db_action for db_action in db_actions}

        self.logger.info(
            "Syncing actions from list",
            repository=db_repo.origin,
            incoming_actions=len(incoming_actions_map),
            existing_actions=len(db_actions_map),
        )

        if not incoming_actions_map:
            if db_actions_map and not allow_delete_all:
                self.logger.error(
                    "Empty registry snapshot; refusing to delete existing actions",
                    repository=db_repo.origin,
                    existing_actions=len(db_actions_map),
                )
                raise RegistryError(
                    "Sync aborted: repository produced no actions; existing actions were preserved."
                )

            if not db_actions_map:
                self.logger.info(
                    "No actions found in repository and none in database; nothing to sync",
                    repository=db_repo.origin,
                )
                return

        n_created = 0
        n_updated = 0
        n_deleted = 0

        for action_name, create_params in incoming_actions_map.items():
            try:
                registry_action = await self.get_action(action_name)
            except RegistryError:
                self.logger.debug(
                    "Action not found, creating",
                    namespace=create_params.namespace,
                    origin=create_params.origin,
                    repository_id=db_repo.id,
                )
                await self.create_action(create_params, commit=commit)
                n_created += 1
            else:
                self.logger.debug(
                    "Action found, updating",
                    namespace=create_params.namespace,
                    origin=create_params.origin,
                    repository_id=db_repo.id,
                )
                # Convert RegistryActionCreate to RegistryActionUpdate
                update_params = RegistryActionUpdate(
                    name=create_params.name,
                    description=create_params.description,
                    interface=create_params.interface,
                    implementation=create_params.implementation,
                    default_title=create_params.default_title,
                    display_group=create_params.display_group,
                    doc_url=create_params.doc_url,
                    author=create_params.author,
                    deprecated=create_params.deprecated,
                    options=create_params.options,
                    secrets=create_params.secrets,
                )
                await self.update_action(registry_action, update_params, commit=commit)
                n_updated += 1
            finally:
                # Mark action as not to delete
                db_actions_map.pop(action_name, None)

        # Remove actions that are marked for deletion
        if db_actions_map:
            self.logger.warning(
                "Removing actions that are no longer in the repository",
                actions=db_actions_map.keys(),
            )
            for action_to_remove in db_actions_map.values():
                await self.delete_action(action_to_remove, commit=commit)
                n_deleted += 1

        self.logger.info(
            "Synced actions from repository",
            repository=db_repo.origin,
            created=n_created,
            updated=n_updated,
            deleted=n_deleted,
        )

    # Legacy method for backward compatibility with in-process sync
    async def upsert_actions_from_repo(
        self,
        repo: Repository,
        db_repo: RegistryRepository,
        *,
        commit: bool = True,
        allow_delete_all: bool = False,
    ) -> None:
        """Upsert actions from a Repository object (legacy in-process flow).

        This method is kept for backward compatibility. New code should use
        the subprocess-based sync flow via sync_actions_from_repository.
        """
        # Convert BoundRegistryAction objects to RegistryActionCreate DTOs
        actions = [
            RegistryActionCreate.from_bound(bound_action, db_repo.id)
            for bound_action in repo.store.values()
        ]
        await self.upsert_actions_from_list(
            actions, db_repo, commit=commit, allow_delete_all=allow_delete_all
        )

    async def load_action_impl(
        self, action_name: str, mode: LoaderMode = "validation"
    ) -> BoundRegistryAction:
        """
        Load the implementation for a registry action.
        """
        action = await self.get_action(action_name)
        bound_action = get_bound_action_impl(action, mode=mode)
        return bound_action

    async def read_action_with_implicit_secrets(
        self, action: RegistryAction
    ) -> RegistryActionRead:
        extra_secrets = await self.fetch_all_action_secrets(action)
        impl = RegistryActionImplValidator.validate_python(action.implementation)
        secrets = {
            RegistrySecretTypeValidator.validate_python(secret)
            for secret in action.secrets or []
        }
        if extra_secrets:
            secrets.update(extra_secrets)
        return RegistryActionRead(
            id=action.id,
            repository_id=action.repository_id,
            name=action.name,
            description=action.description,
            namespace=action.namespace,
            type=typing_cast(RegistryActionType, action.type),
            doc_url=action.doc_url,
            author=action.author,
            deprecated=action.deprecated,
            interface=_db_to_interface(action),
            implementation=impl,
            default_title=action.default_title,
            display_group=action.display_group,
            origin=action.origin,
            options=RegistryActionOptions(**action.options),
            secrets=sorted(
                secrets,
                key=lambda x: x.provider_id if x.type == "oauth" else x.name,
            ),
        )

    async def fetch_all_action_secrets(
        self, action: RegistryAction | PlatformRegistryAction
    ) -> set[RegistrySecretType]:
        """Recursively fetch all secrets from the action and its template steps.

        Args:
            action: The registry action to fetch secrets from

        Returns:
            set[RegistrySecret]: A set of secret names used by the action and its template steps
        """
        secrets: set[RegistrySecretType] = set()
        impl = RegistryActionImplValidator.validate_python(action.implementation)
        if impl.type == "udf":
            if action.secrets:
                secrets.update(
                    RegistrySecretTypeValidator.validate_python(secret)
                    for secret in action.secrets
                )
        elif impl.type == "template":
            ta = impl.template_action
            # Add secrets from the template action itself
            if template_secrets := ta.definition.secrets:
                secrets.update(template_secrets)
            # Recursively fetch secrets from each step
            step_action_names = [step.action for step in ta.definition.steps]
            step_ras = await self.get_actions(step_action_names)
            for step_ra in step_ras:
                step_secrets = await self.fetch_all_action_secrets(step_ra)
                secrets.update(step_secrets)
        return secrets

    @staticmethod
    def aggregate_secrets_from_manifest(
        manifest: RegistryVersionManifest,
        action_name: str,
        visited: set[str] | None = None,
    ) -> list[RegistrySecretType]:
        """Recursively aggregate secrets from an action and its template steps.

        This method traverses template actions in the manifest to collect all
        secrets required by the action and any nested template steps.

        Args:
            manifest: The registry version manifest containing all actions
            action_name: The full action name (e.g., "core.http_request")
            visited: Set of visited action names to prevent infinite recursion

        Returns:
            List of all secrets required by the action and its template steps
        """
        if visited is None:
            visited = set()

        # Prevent infinite recursion
        if action_name in visited:
            return []
        visited.add(action_name)

        manifest_action = manifest.actions.get(action_name)
        if not manifest_action:
            return []

        secrets: list[RegistrySecretType] = []

        # Add direct secrets from this action
        if manifest_action.secrets:
            secrets.extend(manifest_action.secrets)

        # For template actions, recursively collect secrets from steps
        if manifest_action.action_type == "template":
            impl = manifest_action.implementation
            template_action_data = impl.get("template_action")
            if template_action_data:
                definition = template_action_data.get("definition", {})
                steps = definition.get("steps", [])
                for step in steps:
                    step_action_name = step.get("action")
                    if step_action_name:
                        step_secrets = (
                            RegistryActionsService.aggregate_secrets_from_manifest(
                                manifest, step_action_name, visited
                            )
                        )
                        secrets.extend(step_secrets)

        return secrets

    def get_bound(
        self,
        action: RegistryAction,
        mode: LoaderMode = "execution",
    ) -> BoundRegistryAction:
        """Get the bound action for a registry action."""
        return get_bound_action_impl(action, mode=mode)


def error_details_to_message(err: ErrorDetails) -> str:
    loc = err["loc"]
    if isinstance(loc, tuple):
        loc = ", ".join(f"'{i}'" for i in loc)
    match err.get("type"):
        case "missing":
            msg = f"Missing required field(s): {loc}"
        case "extra_forbidden":
            msg = f"Got unexpected field(s): {loc}"
        case _:
            msg = f"{err['msg']}: {loc}"
    return msg


async def validate_action_template(
    action: BoundRegistryAction,
    repo: Repository,
    *,
    check_db: bool = False,
    ra_service: RegistryActionsService | None = None,
    extra_repos: Sequence[Repository] | None = None,
) -> list[RegistryActionValidationErrorInfo]:
    """Validate that a template action is correctly formatted."""
    if not (action.is_template and action.template_action):
        return []
    if check_db and not ra_service:
        raise ValueError("RegistryActionsService is required if check_db is True")
    val_errs: list[RegistryActionValidationErrorInfo] = []
    log = ra_service.logger if ra_service else logger

    defn = action.template_action.definition

    def lookup_extra_action(action_name: str) -> BoundRegistryAction | None:
        if not extra_repos:
            return None
        for extra_repo in extra_repos:
            if action_name in extra_repo.store:
                return extra_repo.store[action_name]
        return None

    # 1. Validate template steps
    for step in defn.steps:
        # (A) Ensure that the step action type exists
        if step.action in repo.store:
            # If this action is already in the repo, we can just use it
            # We will overwrite the action in the DB anyways
            bound_action = repo.store[step.action]
        elif (extra_action := lookup_extra_action(step.action)) is not None:
            bound_action = extra_action
        elif (
            check_db
            and ra_service
            and (reg_action := await ra_service.get_action_or_none(step.action))
            is not None
        ):
            bound_action = get_bound_action_impl(reg_action, mode="validation")
        else:
            # Action not found in the repo or DB
            val_errs.append(
                RegistryActionValidationErrorInfo(
                    loc_primary=f"steps.{step.ref}",
                    loc_secondary=step.action,
                    type=TemplateActionValidationErrorType.ACTION_NOT_FOUND,
                    details=[f"Action `{step.action}` not found in repository."],
                    is_template=action.is_template,
                )
            )
            log.warning(
                "Step action not found, skipping",
                step_ref=step.ref,
                step_action=step.action,
            )
            continue

        # (B) Validate that the step is correctly formatted
        try:
            bound_action.validate_args(args=step.args)
        except RegistryValidationError as e:
            if isinstance(e.err, ValidationError):
                details = []
                for err in e.err.errors():
                    msg = error_details_to_message(err)
                    details.append(msg)
            else:
                details = [str(e.err)] if e.err else []
            val_errs.append(
                RegistryActionValidationErrorInfo(
                    loc_primary=f"steps.{step.ref}",
                    loc_secondary=step.action,
                    type=TemplateActionValidationErrorType.STEP_VALIDATION_ERROR,
                    details=details,
                    is_template=action.is_template,
                )
            )
    # 2. Validate expressions
    validator = TemplateActionExprValidator(
        validation_context=TemplateActionValidationContext(
            expects=defn.expects,
            step_refs={step.ref for step in defn.steps},
        ),
    )
    for step in defn.steps:
        for field, value in step.args.items():
            for expr in extract_expressions(value):
                expr.validate(validator, loc=("steps", step.ref, "args", field))
    for expr in extract_expressions(defn.returns):
        expr.validate(validator, loc=("returns",))
    expr_errs = set(validator.errors())
    if expr_errs:
        log.warning("Expression validation errors", errors=expr_errs)
    val_errs.extend(
        RegistryActionValidationErrorInfo.from_validation_result(
            e, is_template=action.is_template
        )
        for e in expr_errs
    )

    return val_errs


def _implementation_to_interface(
    impl: AnnotatedRegistryActionImpl,
) -> RegistryActionInterface:
    if impl.type == "template":
        expects = create_expectation_model(
            schema=impl.template_action.definition.expects,
            model_name=impl.template_action.definition.action.replace(".", "__"),
        )
        return RegistryActionInterface(
            expects=expects.model_json_schema(),
            returns=impl.template_action.definition.returns,
        )
    else:
        return RegistryActionInterface(expects={}, returns={})


def _db_to_interface(action: RegistryAction) -> RegistryActionInterface:
    match action.implementation:
        case {"type": "template", "template_action": template_action}:
            template = TemplateAction.model_validate(template_action)
            expects = create_expectation_model(
                template.definition.expects,
                template.definition.action.replace(".", "__"),
            )
            intf = RegistryActionInterface(
                expects=expects.model_json_schema(),
                returns=template.definition.returns,
            )
        case {"type": "udf", **_kwargs}:
            intf = RegistryActionInterface(
                expects=action.interface.get("expects", {}),
                returns=action.interface.get("returns", {}),
            )
        case _:
            raise ValueError(f"Unknown implementation type: {action.implementation}")
    return intf
