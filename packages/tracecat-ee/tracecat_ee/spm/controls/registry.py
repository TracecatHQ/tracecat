"""Static SPM control catalog loading and validation."""

from __future__ import annotations

from dataclasses import dataclass
from functools import lru_cache
from importlib import resources
from pathlib import Path
from typing import TYPE_CHECKING, cast

import yaml
from pydantic import ValidationError

from tracecat_ee.spm.schemas import SpmControlRead
from tracecat_ee.spm.types import SpmControlCheck, SpmEnforcementAction

if TYPE_CHECKING:
    from importlib.resources.abc import Traversable
    from os import PathLike

    type ManifestPath = Path | Traversable


@dataclass(frozen=True, slots=True)
class SpmControlCheckDefinition:
    """Registered control check definition."""

    key: SpmControlCheck
    description: str


@dataclass(frozen=True, slots=True)
class SpmControlActionDefinition:
    """Registered control action definition."""

    key: SpmEnforcementAction
    description: str


CHECK_REGISTRY: dict[SpmControlCheck, SpmControlCheckDefinition] = {
    SpmControlCheck.TRUSTED_DIRECTORY_APPROVED: SpmControlCheckDefinition(
        key=SpmControlCheck.TRUSTED_DIRECTORY_APPROVED,
        description="Trusted directories must be approved before local access remains enabled.",
    ),
    SpmControlCheck.ADDITIONAL_DIRECTORY_APPROVED: SpmControlCheckDefinition(
        key=SpmControlCheck.ADDITIONAL_DIRECTORY_APPROVED,
        description="Additional directories must be approved before local access remains enabled.",
    ),
    SpmControlCheck.PERMISSION_CONFIG_APPROVED: SpmControlCheckDefinition(
        key=SpmControlCheck.PERMISSION_CONFIG_APPROVED,
        description="Permission settings must match the approved local Claude configuration.",
    ),
    SpmControlCheck.SANDBOX_CONFIG_APPROVED: SpmControlCheckDefinition(
        key=SpmControlCheck.SANDBOX_CONFIG_APPROVED,
        description="Sandbox settings must match the approved local Claude configuration.",
    ),
    SpmControlCheck.HOOK_APPROVED: SpmControlCheckDefinition(
        key=SpmControlCheck.HOOK_APPROVED,
        description="Hooks must be approved before they remain enabled for Claude.",
    ),
    SpmControlCheck.HOOK_RISK_OK: SpmControlCheckDefinition(
        key=SpmControlCheck.HOOK_RISK_OK,
        description="Hooks must not match risky execution heuristics.",
    ),
    SpmControlCheck.SKILL_APPROVED: SpmControlCheckDefinition(
        key=SpmControlCheck.SKILL_APPROVED,
        description="Skills must be approved before they remain enabled for Claude.",
    ),
    SpmControlCheck.SKILL_RISK_OK: SpmControlCheckDefinition(
        key=SpmControlCheck.SKILL_RISK_OK,
        description="Skills must not match risky content heuristics.",
    ),
    SpmControlCheck.MCP_SERVER_APPROVED: SpmControlCheckDefinition(
        key=SpmControlCheck.MCP_SERVER_APPROVED,
        description="MCP servers must match an approved server-name plus resolved-identity tuple.",
    ),
    SpmControlCheck.MCP_SERVER_VULNERABILITY_OK: SpmControlCheckDefinition(
        key=SpmControlCheck.MCP_SERVER_VULNERABILITY_OK,
        description="MCP server identities must not resolve to vulnerable packages or binaries.",
    ),
    SpmControlCheck.MCP_SERVER_REPUTATION_OK: SpmControlCheckDefinition(
        key=SpmControlCheck.MCP_SERVER_REPUTATION_OK,
        description="MCP server identities must pass configured reputation checks.",
    ),
    SpmControlCheck.INSTRUCTION_FILE_LANGUAGE_ENGLISH: SpmControlCheckDefinition(
        key=SpmControlCheck.INSTRUCTION_FILE_LANGUAGE_ENGLISH,
        description="Claude instruction files must remain English-language when the policy requires it.",
    ),
    SpmControlCheck.INSTRUCTION_FILE_OBFUSCATION_ABSENT: SpmControlCheckDefinition(
        key=SpmControlCheck.INSTRUCTION_FILE_OBFUSCATION_ABSENT,
        description="Claude instruction files must not contain base64, high-entropy, or obfuscated prompt material.",
    ),
    SpmControlCheck.INSTRUCTION_FILE_EXTERNAL_INDICATORS_REPUTATION_OK: SpmControlCheckDefinition(
        key=SpmControlCheck.INSTRUCTION_FILE_EXTERNAL_INDICATORS_REPUTATION_OK,
        description="URLs, domains, and IPs extracted from Claude instruction files must pass reputation checks.",
    ),
}

ACTION_REGISTRY: dict[SpmEnforcementAction, SpmControlActionDefinition] = {
    SpmEnforcementAction.REVOKE_TRUSTED_DIRECTORY: SpmControlActionDefinition(
        key=SpmEnforcementAction.REVOKE_TRUSTED_DIRECTORY,
        description="Remove a trusted directory from writable Claude configuration.",
    ),
    SpmEnforcementAction.REVOKE_ADDITIONAL_DIRECTORY: SpmControlActionDefinition(
        key=SpmEnforcementAction.REVOKE_ADDITIONAL_DIRECTORY,
        description="Remove an additional directory from writable Claude configuration.",
    ),
    SpmEnforcementAction.RECONCILE_PERMISSION_CONFIG: SpmControlActionDefinition(
        key=SpmEnforcementAction.RECONCILE_PERMISSION_CONFIG,
        description="Reconcile writable Claude permission settings to the approved state.",
    ),
    SpmEnforcementAction.RECONCILE_SANDBOX_CONFIG: SpmControlActionDefinition(
        key=SpmEnforcementAction.RECONCILE_SANDBOX_CONFIG,
        description="Reconcile writable Claude sandbox settings to the approved state.",
    ),
    SpmEnforcementAction.DISABLE_HOOK: SpmControlActionDefinition(
        key=SpmEnforcementAction.DISABLE_HOOK,
        description="Disable a Claude hook through writable local configuration.",
    ),
    SpmEnforcementAction.DISABLE_SKILL: SpmControlActionDefinition(
        key=SpmEnforcementAction.DISABLE_SKILL,
        description="Disable a Claude skill through writable local configuration.",
    ),
    SpmEnforcementAction.DISABLE_MCP_SERVER: SpmControlActionDefinition(
        key=SpmEnforcementAction.DISABLE_MCP_SERVER,
        description="Disable a Claude MCP server without mutating project `.mcp.json`.",
    ),
    SpmEnforcementAction.EXCLUDE_INSTRUCTION_FILE: SpmControlActionDefinition(
        key=SpmEnforcementAction.EXCLUDE_INSTRUCTION_FILE,
        description="Exclude a Claude instruction file path through `claudeMdExcludes`.",
    ),
}


def _iter_manifest_paths(
    directory: Traversable | PathLike[str] | str,
) -> list[ManifestPath]:
    if isinstance(directory, Path):
        base = directory
    elif isinstance(directory, str):
        base = Path(directory)
    elif hasattr(directory, "iterdir"):
        traversable_dir = cast("Traversable", directory)
        return sorted(
            [
                entry
                for entry in traversable_dir.iterdir()
                if entry.name.endswith(".yaml")
            ],
            key=lambda entry: entry.name,
        )
    else:
        base = Path(cast("PathLike[str]", directory))
    return sorted(
        (path for path in base.iterdir() if path.suffix == ".yaml"),
        key=lambda path: path.name,
    )


def load_control_catalog_from_directory(
    directory: Traversable | PathLike[str] | str,
) -> tuple[SpmControlRead, ...]:
    """Load and validate an SPM control catalog from YAML manifests."""
    controls: list[SpmControlRead] = []
    seen_ids: set[str] = set()
    manifest_paths = _iter_manifest_paths(directory)
    if not manifest_paths:
        raise ValueError("SPM control catalog is empty")

    for manifest_path in manifest_paths:
        with manifest_path.open("r", encoding="utf-8") as handle:
            raw_manifest = yaml.safe_load(handle)
        if not isinstance(raw_manifest, dict):
            raise ValueError(f"SPM control manifest must be an object: {manifest_path}")
        try:
            control = SpmControlRead.model_validate(raw_manifest)
        except ValidationError as exc:
            for error in exc.errors(include_url=False):
                match error.get("loc"):
                    case ("check",):
                        raise ValueError(
                            f"Unknown SPM control check key: {raw_manifest.get('check')}"
                        ) from exc
                    case ("action",):
                        raise ValueError(
                            "Unknown SPM control action key: "
                            f"{raw_manifest.get('action')}"
                        ) from exc
            raise ValueError(f"Invalid SPM control manifest: {manifest_path}") from exc
        if control.id in seen_ids:
            raise ValueError(f"Duplicate SPM control id: {control.id}")
        if control.check not in CHECK_REGISTRY:
            raise ValueError(f"Unknown SPM control check key: {control.check}")
        if control.action not in ACTION_REGISTRY:
            raise ValueError(f"Unknown SPM control action key: {control.action}")
        controls.append(control)
        seen_ids.add(control.id)
    return tuple(sorted(controls, key=lambda control: control.id))


@lru_cache(maxsize=1)
def get_control_catalog() -> tuple[SpmControlRead, ...]:
    """Load the built-in SPM control catalog."""
    catalog_dir = resources.files("tracecat_ee.spm.controls").joinpath("catalog")
    return load_control_catalog_from_directory(catalog_dir)


def get_control(control_id: str) -> SpmControlRead | None:
    """Fetch a control by id from the static catalog."""
    for control in get_control_catalog():
        if control.id == control_id:
            return control
    return None


# Import-time catalog loading makes invalid manifests fail fast during app startup.
CONTROL_CATALOG = get_control_catalog()
