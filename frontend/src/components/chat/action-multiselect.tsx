"use client"

import { useVirtualizer } from "@tanstack/react-virtual"
import fuzzysort from "fuzzysort"
import { Search, X } from "lucide-react"
import React, { useCallback, useMemo, useRef } from "react"
import type { ControllerRenderProps, FieldValues } from "react-hook-form"
import type { RegistryActionReadMinimal } from "@/client"
import { getIcon } from "@/components/icons"
import { CenteredSpinner } from "@/components/loading/spinner"
import type { Suggestion } from "@/components/tags-input"
import { Badge } from "@/components/ui/badge"
import { Button } from "@/components/ui/button"
import { Checkbox } from "@/components/ui/checkbox"
import { Input } from "@/components/ui/input"
import { isAgentToolSelectable } from "@/lib/agent-tools"
import { useBuilderRegistryActions } from "@/lib/hooks"
import { cn } from "@/lib/utils"

interface ActionMultiselectProps<T extends FieldValues> {
  field: ControllerRenderProps<T>
  searchKeys: (keyof Suggestion)[]
  maxHeight?: string
  className?: string
}

type MultiselectRow =
  | { kind: "header"; namespace: string }
  | { kind: "action"; suggestion: Suggestion }

export function ActionMultiselect<T extends FieldValues>({
  field,
  searchKeys,
  maxHeight = "400px",
  className,
}: ActionMultiselectProps<T>) {
  const { registryActions, registryActionsIsLoading } =
    useBuilderRegistryActions()
  const selectedActions = useMemo<string[]>(() => {
    // Normalize the field value so downstream consumers always receive an array.
    return Array.isArray(field.value) ? field.value : []
  }, [field.value])

  // Search and filter functionality
  const [searchQuery, setSearchQuery] = React.useState("")

  // Map actions to suggestions format for fuzzy search
  const suggestions = useMemo(() => {
    return (
      registryActions
        ?.filter((action) => isAgentToolSelectable(action.action))
        .map((action) => ({
          id: action.action,
          label: action.default_title || action.action,
          value: action.action,
          description: action.description,
          group: action.namespace,
        }))
        .sort((a, b) => a.value.localeCompare(b.value)) || []
    )
  }, [registryActions])

  // Fuzzy search function (same as MultiTagCommandInput)
  const filterActions = useCallback(
    (actions: Suggestion[], search: string) => {
      if (!search.trim()) {
        return actions.map((action) => ({ obj: action, score: 0 }))
      }

      const results = fuzzysort.go<Suggestion>(search, actions, {
        all: true,
        keys: searchKeys,
      })
      return results
    },
    [searchKeys]
  )

  // Apply fuzzy search to get filtered suggestions
  const filteredSuggestions = useMemo(() => {
    if (!suggestions) return []
    return filterActions(suggestions, searchQuery).map((result) => result.obj)
  }, [suggestions, searchQuery, filterActions])

  // Flatten namespace headers and actions into one virtualized row list.
  const rows = useMemo(() => {
    const groups = new Map<string, Suggestion[]>()
    for (const suggestion of filteredSuggestions) {
      const group = suggestion.group || "Other"
      const existing = groups.get(group)
      if (existing) existing.push(suggestion)
      else groups.set(group, [suggestion])
    }
    const flat: MultiselectRow[] = []
    for (const [namespace, actions] of groups) {
      flat.push({ kind: "header", namespace })
      for (const suggestion of actions)
        flat.push({ kind: "action", suggestion })
    }
    return flat
  }, [filteredSuggestions])

  const suggestionsByValue = useMemo(
    () => new Map(suggestions.map((s) => [s.value, s])),
    [suggestions]
  )
  const selectedSet = useMemo(() => new Set(selectedActions), [selectedActions])
  const listRef = useRef<HTMLDivElement>(null)
  const virtualizer = useVirtualizer({
    count: rows.length,
    getScrollElement: () => listRef.current,
    estimateSize: (i) => (rows[i]?.kind === "header" ? 28 : 72),
    getItemKey: (i) => {
      const row = rows[i]
      return row.kind === "header"
        ? `header:${row.namespace}`
        : row.suggestion.value
    },
    overscan: 6,
  })

  // build map of namespaces to registry actions
  const namespaceMap = useMemo(() => {
    return (
      registryActions?.reduce(
        (acc, action) => {
          acc[action.namespace] = action
          return acc
        },
        {} as Record<string, RegistryActionReadMinimal>
      ) || {}
    )
  }, [registryActions])

  function renderSuggestion(suggestion: Suggestion) {
    const isSelected = selectedSet.has(suggestion.value)
    return (
      <div
        className={cn(
          "flex items-start space-x-3 p-3 rounded-lg transition-all duration-200 cursor-pointer hover:bg-accent",
          isSelected
            ? "bg-blue-50 dark:bg-blue-950 border-blue-200 dark:border-blue-800"
            : "border-border"
        )}
      >
        <Checkbox
          checked={isSelected}
          onCheckedChange={(checked) => {
            const currentSelections = Array.isArray(field.value)
              ? field.value
              : []
            if (checked === true) {
              field.onChange([...currentSelections, suggestion.value])
              return
            }
            const nextSelections = currentSelections.filter(
              (value: string) => value !== suggestion.value
            )
            field.onChange(nextSelections)
          }}
          onClick={(e) => e.stopPropagation()}
          className="mt-1"
        />
        <div className="flex-1 min-w-0">
          <div className="flex items-center space-x-2 mb-1">
            <div className="size-6">
              {getIcon(suggestion.value, { className: "size-6" })}
            </div>
            <span className="font-medium text-xs">{suggestion.label}</span>
          </div>
          <p className="text-xs text-muted-foreground line-clamp-2">
            {suggestion.description}
          </p>
        </div>
      </div>
    )
  }

  if (registryActionsIsLoading) {
    return <CenteredSpinner />
  }

  return (
    <div className={cn("grid grid-cols-1 lg:grid-cols-3 gap-4", className)}>
      <div className="lg:col-span-2 space-y-4">
        <div className="space-y-4">
          <div className="flex items-center justify-between">
            <h3 className="text-lg font-semibold">Available tools</h3>
            <Badge variant="secondary">{selectedActions.length} selected</Badge>
          </div>
          <div className="relative">
            <Search className="absolute left-3 top-1/2 transform -translate-y-1/2 text-muted-foreground h-4 w-4" />
            <Input
              placeholder="Search actions..."
              value={searchQuery}
              onChange={(e) => setSearchQuery(e.target.value)}
              className="pl-10"
            />
          </div>
        </div>
        <div ref={listRef} className="overflow-y-auto" style={{ maxHeight }}>
          {rows.length > 0 && (
            <div
              className="relative w-full"
              style={{ height: virtualizer.getTotalSize() }}
            >
              {virtualizer.getVirtualItems().map((item) => {
                const row = rows[item.index]
                return (
                  <div
                    key={item.key}
                    data-index={item.index}
                    ref={virtualizer.measureElement}
                    className="absolute left-0 top-0 w-full pb-3"
                    style={{ transform: `translateY(${item.start}px)` }}
                  >
                    {row.kind === "header" ? (
                      <div className="pt-1 text-xs font-medium capitalize">
                        {namespaceMap[row.namespace]?.display_group ||
                          row.namespace}
                      </div>
                    ) : (
                      renderSuggestion(row.suggestion)
                    )}
                  </div>
                )
              })}
            </div>
          )}
          {filteredSuggestions.length === 0 && searchQuery && (
            <div className="text-center py-8">
              <p className="text-muted-foreground">
                No actions found for "{searchQuery}"
              </p>
              <button
                onClick={() => setSearchQuery("")}
                className="text-blue-600 hover:text-blue-700 text-sm mt-2"
              >
                Clear search
              </button>
            </div>
          )}
          {!suggestions?.length && (
            <div className="text-center py-8">
              <p className="text-muted-foreground">No actions available</p>
            </div>
          )}
        </div>
      </div>

      <div className="lg:col-span-1 space-y-4">
        <div className="text-sm font-medium">Selected Tools</div>
        <div className="space-y-4">
          {selectedActions.length === 0 ? (
            <p className="text-sm text-muted-foreground text-center py-8">
              No actions selected yet
            </p>
          ) : (
            <div className="space-y-3">
              {selectedActions.map((actionId: string) => {
                const suggestion = suggestionsByValue.get(actionId)
                return suggestion ? (
                  <div
                    key={actionId}
                    className="flex items-center space-x-2 p-2 bg-muted rounded group"
                  >
                    {getIcon(suggestion.value, { className: "size-6" })}
                    <span className="text-sm font-medium truncate flex-1">
                      {suggestion.label}
                    </span>
                    <Button
                      onClick={() => {
                        const currentSelections = Array.isArray(field.value)
                          ? field.value
                          : []
                        const nextSelections = currentSelections.filter(
                          (value: string) => value !== actionId
                        )
                        field.onChange(nextSelections)
                      }}
                      variant="ghost"
                      size="icon"
                      className="opacity-0 group-hover:opacity-100 transition-opacity h-6 w-6"
                      type="button"
                    >
                      <X className="h-3 w-3 text-muted-foreground" />
                    </Button>
                  </div>
                ) : null
              })}
              <div className="pt-3 border-t">
                <p className="text-xs text-muted-foreground">
                  {selectedActions.length} action
                  {selectedActions.length !== 1 ? "s" : ""} selected
                </p>
              </div>
            </div>
          )}
        </div>
      </div>
    </div>
  )
}
