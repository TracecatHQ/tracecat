"use client"

import { useCallback, useMemo, useState } from "react"
import type { RoleReadWithScopes, ScopeRead } from "@/client"
import {
  getScopesForLevel,
  ScopeCategoryRow,
} from "@/components/rbac/scope-category-row"
import { Button } from "@/components/ui/button"
import {
  DialogContent,
  DialogDescription,
  DialogFooter,
  DialogHeader,
  DialogTitle,
} from "@/components/ui/dialog"
import { Input } from "@/components/ui/input"
import { Label } from "@/components/ui/label"
import {
  getCategoryScopes,
  type PermissionLevel,
  RESOURCE_CATEGORIES,
} from "@/lib/rbac"

export interface RoleFormDialogProps {
  title: string
  description: string
  scopes: ScopeRead[]
  initialData?: RoleReadWithScopes
  onSubmit: (
    name: string,
    description: string,
    scopeIds: string[]
  ) => Promise<void>
  isPending: boolean
  onOpenChange: (open: boolean) => void
  /** Filter resource categories to show (defaults to all) */
  categoryFilter?: (categoryKey: string) => boolean
}

export function RoleFormDialog({
  title,
  description,
  scopes,
  initialData,
  onSubmit,
  isPending,
  onOpenChange,
  categoryFilter,
}: RoleFormDialogProps) {
  const [name, setName] = useState(initialData?.name ?? "")
  const [roleDescription, setRoleDescription] = useState(
    initialData?.description ?? ""
  )
  const [selectedScopeIds, setSelectedScopeIds] = useState<Set<string>>(
    new Set(initialData?.scopes?.map((s) => s.id) ?? [])
  )

  const toggleScope = useCallback((scopeId: string, checked: boolean) => {
    setSelectedScopeIds((prev) => {
      const next = new Set(prev)
      if (checked) {
        next.add(scopeId)
      } else {
        next.delete(scopeId)
      }
      return next
    })
  }, [])

  const handleLevelChange = useCallback(
    (categoryResources: string[], level: PermissionLevel) => {
      setSelectedScopeIds((prev) => {
        const next = new Set(prev)
        // Get all scopes for this category
        const categoryScopes = getCategoryScopes(categoryResources, scopes)
        // Remove all scopes from this category
        for (const scope of categoryScopes) {
          next.delete(scope.id)
        }
        // If level is not "none", add the appropriate scopes
        if (level !== "none" && level !== "mixed") {
          const scopesToAdd = getScopesForLevel(
            categoryResources,
            scopes,
            level
          )
          for (const id of scopesToAdd) {
            next.add(id)
          }
        }
        return next
      })
    },
    [scopes]
  )

  const handleSubmit = async (e: React.FormEvent) => {
    e.preventDefault()
    if (!name.trim()) return
    await onSubmit(
      name.trim(),
      roleDescription.trim(),
      Array.from(selectedScopeIds)
    )
  }

  // Filter categories if a filter is provided
  const filteredCategories = useMemo(() => {
    if (!categoryFilter) return RESOURCE_CATEGORIES
    return Object.fromEntries(
      Object.entries(RESOURCE_CATEGORIES).filter(([key]) => categoryFilter(key))
    )
  }, [categoryFilter])

  // Count scopes by category for the summary
  const scopeCounts = useMemo(() => {
    let total = 0
    for (const category of Object.values(filteredCategories)) {
      const categoryScopes = getCategoryScopes(category.resources, scopes)
      total += categoryScopes.filter((s) => selectedScopeIds.has(s.id)).length
    }
    return total
  }, [scopes, selectedScopeIds, filteredCategories])

  return (
    <DialogContent className="max-w-3xl max-h-[85vh] flex flex-col">
      <form onSubmit={handleSubmit} className="flex flex-col flex-1 min-h-0">
        <DialogHeader>
          <DialogTitle>{title}</DialogTitle>
          <DialogDescription>{description}</DialogDescription>
        </DialogHeader>

        <div className="flex-1 min-h-0 space-y-4 py-4 overflow-hidden">
          <div className="grid gap-4 sm:grid-cols-2">
            <div className="space-y-2">
              <Label htmlFor="role-name">Role name</Label>
              <Input
                id="role-name"
                placeholder="e.g., Security Analyst"
                value={name}
                onChange={(e) => setName(e.target.value)}
                required
              />
            </div>
            <div className="space-y-2">
              <Label htmlFor="role-description">Description</Label>
              <Input
                id="role-description"
                placeholder="Optional description"
                value={roleDescription}
                onChange={(e) => setRoleDescription(e.target.value)}
              />
            </div>
          </div>

          <div className="flex-1 min-h-0 space-y-2">
            <div className="flex items-center justify-between">
              <Label>Permissions ({scopeCounts} scopes selected)</Label>
            </div>
            <div className="text-xs text-muted-foreground mb-2">
              Set permission levels by category, or expand to select individual
              scopes.
            </div>
            <div className="h-[400px] overflow-y-auto rounded-md border">
              <div className="divide-y divide-border/50">
                {Object.entries(filteredCategories).map(([key, category]) => (
                  <ScopeCategoryRow
                    key={key}
                    categoryKey={key}
                    category={category}
                    scopes={scopes}
                    selectedScopeIds={selectedScopeIds}
                    onScopeToggle={toggleScope}
                    onLevelChange={handleLevelChange}
                  />
                ))}
              </div>
            </div>
          </div>
        </div>

        <DialogFooter>
          <Button
            type="button"
            variant="outline"
            onClick={() => onOpenChange(false)}
          >
            Cancel
          </Button>
          <Button type="submit" disabled={!name.trim() || isPending}>
            {isPending
              ? initialData
                ? "Saving..."
                : "Creating..."
              : initialData
                ? "Save changes"
                : "Create role"}
          </Button>
        </DialogFooter>
      </form>
    </DialogContent>
  )
}
