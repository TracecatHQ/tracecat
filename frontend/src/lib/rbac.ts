import type { ScopeRead } from "@/client"

// Permission levels for resource categories
export type PermissionLevel =
  | "none"
  | "read"
  | "write"
  | "execute"
  | "admin"
  | "mixed"

// Actions included at each permission level (cumulative)
export const LEVEL_ACTIONS: Record<
  Exclude<PermissionLevel, "mixed">,
  string[]
> = {
  none: [],
  read: ["read"],
  write: ["read", "create", "update"],
  execute: ["read", "execute"],
  admin: [
    "read",
    "create",
    "update",
    "delete",
    "invite",
    "remove",
    "manage",
    "execute",
  ],
}

// Resource category definitions with display names
export const RESOURCE_CATEGORIES: Record<
  string,
  { label: string; description: string; resources: string[] }
> = {
  workflows: {
    label: "Workflows",
    description: "Workflow automation and execution",
    resources: ["workflow", "schedule"],
  },
  cases: {
    label: "Cases",
    description: "Case management and tracking",
    resources: ["case"],
  },
  data: {
    label: "Data",
    description: "Tables, tags, and variables",
    resources: ["table", "tag", "variable"],
  },
  agents: {
    label: "Agents",
    description:
      "AI agent configuration, execution, and per-preset access controls",
    resources: ["agent"],
  },
  secrets: {
    label: "Secrets",
    description: "Credential and secret management",
    resources: ["secret"],
  },
  workspace: {
    label: "Workspace",
    description: "Workspace settings, members, and access control",
    resources: ["workspace", "workspace:member", "workspace:rbac"],
  },
  organization: {
    label: "Organization",
    description: "Organization settings, members, and RBAC",
    resources: ["org"],
  },
  actions: {
    label: "Actions",
    description: "Registry action execution permissions",
    resources: ["action"],
  },
}

// Display labels for permission levels
export const LEVEL_LABELS: Record<PermissionLevel, string> = {
  none: "None",
  read: "Read",
  write: "Write",
  execute: "Execute",
  admin: "Admin",
  mixed: "Custom",
}

function isActionScopeResource(resource: string): boolean {
  return resource === "action" || resource.startsWith("action:")
}

function extractNamespace(value: string): string | null {
  const normalized = value.trim()
  if (!normalized) return null
  const delimiter = [".", "/", ":"].find((separator) =>
    normalized.includes(separator)
  )
  if (!delimiter) return normalized
  return normalized.slice(0, normalized.indexOf(delimiter))
}

function getActionScopeTarget(scope: ScopeRead): string | null {
  const sourceRef = scope.source_ref?.trim()
  if (sourceRef) return sourceRef

  if (scope.resource.startsWith("action:")) {
    const resourceTarget = scope.resource.slice("action:".length).trim()
    if (resourceTarget) return resourceTarget
  }

  if (scope.name.startsWith("action:") && scope.name.endsWith(":execute")) {
    const nameTarget = scope.name.slice("action:".length, -":execute".length)
    if (nameTarget.trim()) return nameTarget.trim()
  }

  return null
}

export function getScopeActionNamespace(scope: ScopeRead): string | null {
  if (!isActionScopeResource(scope.resource)) return null
  const target = getActionScopeTarget(scope)
  if (!target) return null
  return extractNamespace(target)
}

export function getScopeActionLabel(scope: ScopeRead): string {
  if (!isActionScopeResource(scope.resource)) return scope.action

  const namespace = getScopeActionNamespace(scope)
  const target = getActionScopeTarget(scope)
  if (!target || !namespace || target === namespace) {
    return scope.action
  }

  const namespacePrefix = `${namespace}.`
  if (target.startsWith(namespacePrefix)) {
    return target.slice(namespacePrefix.length)
  }
  const colonPrefix = `${namespace}:`
  if (target.startsWith(colonPrefix)) {
    return target.slice(colonPrefix.length)
  }
  const slashPrefix = `${namespace}/`
  if (target.startsWith(slashPrefix)) {
    return target.slice(slashPrefix.length)
  }

  return target
}

/**
 * Get scopes that match a category's resources
 */
export function getCategoryScopes(
  categoryResources: string[],
  scopes: ScopeRead[]
): ScopeRead[] {
  return scopes.filter((s) =>
    categoryResources.some(
      (r) => s.resource === r || s.resource.startsWith(`${r}:`)
    )
  )
}

/**
 * Get scopes that should be selected for a given permission level
 */
export function getScopesForLevel(
  categoryResources: string[],
  scopes: ScopeRead[],
  level: PermissionLevel
): string[] {
  if (level === "none" || level === "mixed") return []

  const categoryScopes = getCategoryScopes(categoryResources, scopes)
  const actionsForLevel = LEVEL_ACTIONS[level as keyof typeof LEVEL_ACTIONS]
  if (!actionsForLevel) return []

  return categoryScopes
    .filter((s) => {
      const action = s.action.split(":")[0]
      return actionsForLevel.includes(action)
    })
    .map((s) => s.id)
}

/**
 * Determine the current permission level for a category based on selected scopes
 * This is the inverse of getScopesForLevel - it checks if selected scopes match a level exactly
 */
export function getCategoryPermissionLevel(
  categoryResources: string[],
  scopes: ScopeRead[],
  selectedScopeIds: Set<string>
): PermissionLevel {
  const categoryScopes = getCategoryScopes(categoryResources, scopes)

  if (categoryScopes.length === 0) return "none"

  const selectedInCategory = categoryScopes.filter((s) =>
    selectedScopeIds.has(s.id)
  )

  if (selectedInCategory.length === 0) return "none"
  if (selectedInCategory.length === categoryScopes.length) return "admin"

  // Check each level by comparing selected scopes to expected scopes for that level
  const selectedCategoryIds = new Set(selectedInCategory.map((s) => s.id))
  const expectedReadScopeIds = new Set(
    getScopesForLevel(categoryResources, scopes, "read")
  )

  for (const level of ["write", "execute", "read"] as const) {
    const expectedScopeIds = new Set(
      getScopesForLevel(categoryResources, scopes, level)
    )
    if (expectedScopeIds.size === 0) continue

    // Skip redundant levels (e.g., categories without any `execute` scopes where
    // `execute` would otherwise match exactly the `read` scope set).
    if (level !== "read") {
      if (
        expectedScopeIds.size === expectedReadScopeIds.size &&
        [...expectedScopeIds].every((id) => expectedReadScopeIds.has(id))
      ) {
        continue
      }
    }

    // Exact match check
    if (
      expectedScopeIds.size === selectedCategoryIds.size &&
      [...expectedScopeIds].every((id) => selectedCategoryIds.has(id))
    ) {
      return level
    }
  }

  return "mixed"
}

/**
 * Group scopes by their resource type
 */
export function groupScopesByResource(
  scopes: ScopeRead[]
): Record<string, ScopeRead[]> {
  function getGroupKey(scope: ScopeRead): string {
    if (scope.resource !== "agent:preset") {
      return scope.resource
    }

    const sourceRef = scope.source_ref?.trim()
    if (!sourceRef || !sourceRef.startsWith("agent_preset:")) {
      return scope.resource
    }

    const presetSlug = sourceRef.slice("agent_preset:".length)
    if (!presetSlug) {
      return scope.resource
    }
    if (presetSlug === "wildcard") {
      return "agent:preset:*"
    }
    return `agent:preset:${presetSlug}`
  }

  return scopes.reduce(
    (acc, scope) => {
      const groupKey = getGroupKey(scope)
      if (!acc[groupKey]) {
        acc[groupKey] = []
      }
      acc[groupKey].push(scope)
      return acc
    },
    {} as Record<string, ScopeRead[]>
  )
}
