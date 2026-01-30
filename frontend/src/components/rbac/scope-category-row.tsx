"use client"

import { ChevronDownIcon, ChevronRightIcon } from "lucide-react"
import { memo, useCallback, useMemo, useState } from "react"
import type { ScopeRead } from "@/client"
import { Checkbox } from "@/components/ui/checkbox"
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
  onScopeToggle: (scopeId: string) => void
  onLevelChange: (categoryResources: string[], level: PermissionLevel) => void
}

export function ScopeCategoryRow({
  categoryKey,
  category,
  scopes,
  selectedScopeIds,
  onScopeToggle,
  onLevelChange,
}: ScopeCategoryRowProps) {
  const [isExpanded, setIsExpanded] = useState(false)

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

  // Group scopes by resource for expanded view
  const scopesByResource = useMemo(
    () => groupScopesByResource(categoryScopes),
    [categoryScopes]
  )

  const handleLevelChange = useCallback(
    (newLevel: string) => {
      const level = newLevel as PermissionLevel
      if (level === "mixed") return // Can't set to mixed directly

      // Pass category resources to parent - it will handle the level change logic
      onLevelChange(category.resources, level)
    },
    [category.resources, onLevelChange]
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
        <Select value={currentLevel} onValueChange={handleLevelChange}>
          <SelectTrigger className="w-[110px] h-8 text-xs">
            <SelectValue />
          </SelectTrigger>
          <SelectContent>
            <SelectItem value="none">None</SelectItem>
            <SelectItem value="read">Read</SelectItem>
            {categoryKey !== "organization" && (
              <SelectItem value="write">Write</SelectItem>
            )}
            {(categoryKey === "workflows" ||
              categoryKey === "agents" ||
              categoryKey === "actions") && (
              <SelectItem value="execute">Execute</SelectItem>
            )}
            <SelectItem value="admin">Admin</SelectItem>
            {currentLevel === "mixed" && (
              <SelectItem value="mixed" disabled>
                Custom
              </SelectItem>
            )}
          </SelectContent>
        </Select>
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
}

// Memoized checkbox to prevent re-render cascades
const ScopeCheckbox = memo(function ScopeCheckbox({
  scope,
  isSelected,
  onToggle,
}: {
  scope: ScopeRead
  isSelected: boolean
  onToggle: (scopeId: string) => void
}) {
  const handleClick = useCallback(() => {
    onToggle(scope.id)
  }, [scope.id, onToggle])

  return (
    <button
      type="button"
      className="flex items-center gap-1.5 cursor-pointer text-xs"
      onClick={handleClick}
    >
      <Checkbox checked={isSelected} className="size-3.5 pointer-events-none" />
      <code
        className={cn(
          "text-[10px]",
          isSelected ? "text-foreground" : "text-muted-foreground"
        )}
      >
        {scope.action}
      </code>
    </button>
  )
})

// Re-export for convenience
export { getScopesForLevel }
