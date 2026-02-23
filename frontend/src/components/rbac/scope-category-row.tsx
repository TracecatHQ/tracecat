"use client"

import { CheckIcon, ChevronDownIcon, ChevronRightIcon } from "lucide-react"
import { memo, useCallback, useMemo, useState } from "react"
import type { ScopeRead } from "@/client"
import {
  Select,
  SelectContent,
  SelectItem,
  SelectTrigger,
  SelectValue,
} from "@/components/ui/select"
import {
  getCategoryPermissionLevel,
  getCategoryScopes,
  getScopeActionLabel,
  getScopeActionNamespace,
  getScopesForLevel,
  groupScopesByResource,
  LEVEL_LABELS,
  type PermissionLevel,
} from "@/lib/rbac"
import { cn } from "@/lib/utils"

export interface ScopeCategoryRowProps {
  categoryKey: string
  category: { label: string; description: string; resources: string[] }
  scopes: ScopeRead[]
  selectedScopeIds: Set<string>
  onScopeToggle: (scopeId: string, checked: boolean) => void
  onLevelChange: (categoryResources: string[], level: PermissionLevel) => void
}

export const ScopeCategoryRow = memo(function ScopeCategoryRow({
  categoryKey,
  category,
  scopes,
  selectedScopeIds,
  onScopeToggle,
  onLevelChange,
}: ScopeCategoryRowProps) {
  const [isExpanded, setIsExpanded] = useState(false)
  const isActionsCategory = categoryKey === "actions"

  // Get scopes for this category
  const categoryScopes = useMemo(
    () => getCategoryScopes(category.resources, scopes),
    [scopes, category.resources]
  )

  // Determine current permission level
  const currentLevel = useMemo(
    () =>
      getCategoryPermissionLevel(category.resources, scopes, selectedScopeIds),
    [category.resources, scopes, selectedScopeIds]
  )

  // Group scopes by resource for expanded view (sorted for predictable ordering)
  const scopesByResource = useMemo(() => {
    const groupedScopes = groupScopesByResource(categoryScopes)
    const sortedEntries = Object.entries(groupedScopes).sort(
      ([resourceA], [resourceB]) => resourceA.localeCompare(resourceB)
    )
    const sortedScopesByResource: Record<string, ScopeRead[]> = {}
    for (const [resource, resourceScopes] of sortedEntries) {
      sortedScopesByResource[resource] = [...resourceScopes].sort(
        (scopeA, scopeB) =>
          getScopeActionLabel(scopeA).localeCompare(getScopeActionLabel(scopeB))
      )
    }
    return sortedScopesByResource
  }, [categoryScopes])

  const actionNamespaces = useMemo(() => {
    if (!isActionsCategory) return []
    const namespaces = new Set<string>()
    for (const scope of categoryScopes) {
      const namespace = getScopeActionNamespace(scope)
      if (namespace) {
        namespaces.add(namespace)
      }
    }
    return Array.from(namespaces).sort((a, b) => a.localeCompare(b))
  }, [categoryScopes, isActionsCategory])

  const actionNamespaceValue = useMemo(() => {
    if (!isActionsCategory) return "all"
    if (categoryScopes.length === 0) return "none"

    const selectedActionScopes = categoryScopes.filter((scope) =>
      selectedScopeIds.has(scope.id)
    )

    if (selectedActionScopes.length === 0) return "none"
    if (selectedActionScopes.length === categoryScopes.length) return "all"

    const selectedNamespaces = new Set(
      selectedActionScopes
        .map((scope) => getScopeActionNamespace(scope))
        .filter((namespace): namespace is string => Boolean(namespace))
    )

    if (selectedNamespaces.size !== 1) return "custom"
    const [namespace] = Array.from(selectedNamespaces)

    const namespaceScopeIds = categoryScopes
      .filter((scope) => getScopeActionNamespace(scope) === namespace)
      .map((scope) => scope.id)
    const selectedIds = new Set(selectedActionScopes.map((scope) => scope.id))

    const isExactNamespaceSelection =
      namespaceScopeIds.length === selectedIds.size &&
      namespaceScopeIds.every((scopeId) => selectedIds.has(scopeId))

    return isExactNamespaceSelection ? namespace : "custom"
  }, [categoryScopes, isActionsCategory, selectedScopeIds])

  const handleLevelChange = useCallback(
    (newLevel: string) => {
      const level = newLevel as PermissionLevel
      if (level === "mixed") return // Can't set to mixed directly

      // Pass category resources to parent - it will handle the level change logic
      onLevelChange(category.resources, level)
    },
    [category.resources, onLevelChange]
  )

  const handleActionNamespaceChange = useCallback(
    (value: string) => {
      if (value === "custom") return

      if (value === "none") {
        for (const scope of categoryScopes) {
          onScopeToggle(scope.id, false)
        }
        return
      }

      if (value === "all") {
        for (const scope of categoryScopes) {
          onScopeToggle(scope.id, true)
        }
        return
      }

      for (const scope of categoryScopes) {
        const namespace = getScopeActionNamespace(scope)
        onScopeToggle(scope.id, namespace === value)
      }
    },
    [categoryScopes, onScopeToggle]
  )

  if (categoryScopes.length === 0) return null

  return (
    <div className="border-b border-border/50 last:border-b-0">
      <div className="flex items-center gap-3 px-4 py-2.5">
        <button
          type="button"
          onClick={() => setIsExpanded(!isExpanded)}
          className="flex items-center text-muted-foreground hover:text-foreground"
        >
          {isExpanded ? (
            <ChevronDownIcon className="size-4" />
          ) : (
            <ChevronRightIcon className="size-4" />
          )}
        </button>
        <div className="flex-1 min-w-0">
          <div className="flex items-center gap-2">
            <span className="font-medium text-sm">{category.label}</span>
            {currentLevel !== "none" && (
              <span
                className={cn(
                  "rounded px-1.5 py-0.5 text-[10px] font-medium",
                  currentLevel === "admin"
                    ? "bg-amber-500/10 text-amber-600"
                    : currentLevel === "mixed"
                      ? "bg-violet-500/10 text-violet-600"
                      : "bg-muted text-muted-foreground"
                )}
              >
                {LEVEL_LABELS[currentLevel]}
              </span>
            )}
          </div>
          <p className="text-xs text-muted-foreground truncate">
            {category.description}
          </p>
        </div>
        {isActionsCategory ? (
          <Select
            value={actionNamespaceValue}
            onValueChange={handleActionNamespaceChange}
          >
            <SelectTrigger className="w-[140px] h-8 text-xs">
              <SelectValue placeholder="Namespace" />
            </SelectTrigger>
            <SelectContent>
              <SelectItem value="none">None</SelectItem>
              <SelectItem value="all">All namespaces</SelectItem>
              {actionNamespaces.map((namespace) => (
                <SelectItem key={namespace} value={namespace}>
                  {namespace}
                </SelectItem>
              ))}
              <SelectItem value="custom" disabled>
                Custom
              </SelectItem>
            </SelectContent>
          </Select>
        ) : (
          <Select value={currentLevel} onValueChange={handleLevelChange}>
            <SelectTrigger className="w-[110px] h-8 text-xs">
              <SelectValue />
            </SelectTrigger>
            <SelectContent>
              <SelectItem value="none">None</SelectItem>
              <SelectItem value="read">Read</SelectItem>
              {(categoryKey !== "organization" || currentLevel === "write") && (
                <SelectItem value="write">Write</SelectItem>
              )}
              {(categoryKey === "workflows" || categoryKey === "agents") && (
                <SelectItem value="execute">Execute</SelectItem>
              )}
              <SelectItem value="admin">Admin</SelectItem>
              <SelectItem value="mixed" disabled>
                Custom
              </SelectItem>
            </SelectContent>
          </Select>
        )}
      </div>

      {isExpanded && (
        <div className="px-4 pb-2.5 pl-11 space-y-2">
          {Object.entries(scopesByResource).map(
            ([resource, resourceScopes]) => (
              <div key={resource} className="space-y-1">
                <div className="text-[10px] font-medium text-muted-foreground uppercase tracking-wide">
                  {resource}
                </div>
                <div className="flex flex-wrap gap-x-3 gap-y-1">
                  {resourceScopes.map((scope) => (
                    <ScopeCheckbox
                      key={scope.id}
                      scope={scope}
                      isSelected={selectedScopeIds.has(scope.id)}
                      onToggle={onScopeToggle}
                      label={getScopeActionLabel(scope)}
                    />
                  ))}
                </div>
              </div>
            )
          )}
        </div>
      )}
    </div>
  )
})

// Memoized checkbox to prevent re-render cascades
const ScopeCheckbox = memo(function ScopeCheckbox({
  scope,
  isSelected,
  onToggle,
  label,
}: {
  scope: ScopeRead
  isSelected: boolean
  onToggle: (scopeId: string, checked: boolean) => void
  label: string
}) {
  const handleClick = useCallback(() => {
    onToggle(scope.id, !isSelected)
  }, [isSelected, onToggle, scope.id])

  return (
    <button
      type="button"
      className="flex items-center gap-1.5 cursor-pointer text-xs"
      onClick={handleClick}
    >
      <span
        className={cn(
          "flex size-3.5 shrink-0 items-center justify-center rounded-sm border",
          isSelected
            ? "border-primary bg-primary text-primary-foreground"
            : "border-muted-foreground"
        )}
      >
        {isSelected && <CheckIcon className="size-3" strokeWidth={3} />}
      </span>
      <code
        className={cn(
          "text-[10px]",
          isSelected ? "text-foreground" : "text-muted-foreground"
        )}
      >
        {label}
      </code>
    </button>
  )
})

// Re-export for convenience
export { getScopesForLevel }
