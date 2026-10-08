import type { SyncResourceType } from "@/client"

export interface WorkspaceSyncResourceTypeMeta {
  label: string
  abbr: string
  root: string
  summary: string
}

/**
 * Display metadata per syncable resource type, in projection order. `root` is
 * the Git directory each type serializes to; `summary` names the sub-resources
 * that travel with it. Keep this as the frontend source of truth until the
 * preview API returns manifest resource roots directly.
 */
export const WORKSPACE_SYNC_RESOURCE_TYPE_META: Record<
  SyncResourceType,
  WorkspaceSyncResourceTypeMeta
> = {
  workflow: {
    label: "Workflows",
    abbr: "WF",
    root: "workflows",
    summary: "Definition, tags, webhook and case triggers",
  },
  agent_preset: {
    label: "Agent presets",
    abbr: "AG",
    root: "agent_presets",
    summary: "Instructions, model, skills and subagents",
  },
  skill: {
    label: "Skills",
    abbr: "SK",
    root: "skills",
    summary: "Manifest and file contents",
  },
  table: {
    label: "Tables",
    abbr: "TB",
    root: "tables",
    summary: "Table metadata and schema columns",
  },
  case_field: {
    label: "Case fields",
    abbr: "CF",
    root: "case_fields",
    summary: "Type, kind and select options",
  },
  case_tag: {
    label: "Case tags",
    abbr: "CT",
    root: "case_tags",
    summary: "Name and color",
  },
  case_dropdown: {
    label: "Case dropdowns",
    abbr: "CD",
    root: "case_dropdowns",
    summary: "Options, order and icons",
  },
  case_duration: {
    label: "Case durations",
    abbr: "CR",
    root: "case_durations",
    summary: "Start and end anchors",
  },
  variable: {
    label: "Variables",
    abbr: "VR",
    root: "variables",
    summary: "Keys and tags; values redacted",
  },
  secret_metadata: {
    label: "Secret metadata",
    abbr: "SC",
    root: "secret_metadata",
    summary: "Key names and type; values never synced",
  },
}

export const WORKSPACE_SYNC_RESOURCE_TYPE_ORDER = Object.keys(
  WORKSPACE_SYNC_RESOURCE_TYPE_META
) as SyncResourceType[]

export const WORKSPACE_SYNC_ROOT_TO_RESOURCE_TYPE: Record<
  string,
  SyncResourceType
> = Object.fromEntries(
  WORKSPACE_SYNC_RESOURCE_TYPE_ORDER.map((type) => [
    WORKSPACE_SYNC_RESOURCE_TYPE_META[type].root,
    type,
  ])
)

/**
 * Returns the human label for a sync resource type, preserving unknown resource
 * types for forward-compatible rendering.
 */
export function getWorkspaceSyncResourceLabel(resourceType: string): string {
  if (
    Object.prototype.hasOwnProperty.call(
      WORKSPACE_SYNC_RESOURCE_TYPE_META,
      resourceType
    )
  ) {
    return WORKSPACE_SYNC_RESOURCE_TYPE_META[resourceType as SyncResourceType]
      .label
  }
  return resourceType
}

/**
 * Returns the two-letter tile for a sync resource type, or the first two
 * letters of an unknown type.
 */
export function getWorkspaceSyncResourceAbbr(resourceType: string): string {
  if (
    Object.prototype.hasOwnProperty.call(
      WORKSPACE_SYNC_RESOURCE_TYPE_META,
      resourceType
    )
  ) {
    return WORKSPACE_SYNC_RESOURCE_TYPE_META[resourceType as SyncResourceType]
      .abbr
  }
  return resourceType.slice(0, 2).toUpperCase()
}
