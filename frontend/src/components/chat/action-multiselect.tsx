"use client"

import fuzzysort from "fuzzysort"
import { Search, X } from "lucide-react"
import React, { useCallback, useMemo } from "react"
import type { ControllerRenderProps, FieldValues } from "react-hook-form"
import type { RegistryActionReadMinimal } from "@/client"
import { getIcon } from "@/components/icons"
import { CenteredSpinner } from "@/components/loading/spinner"
import type { Suggestion } from "@/components/tags-input"
import { Badge } from "@/components/ui/badge"
import { Button } from "@/components/ui/button"
import { Checkbox } from "@/components/ui/checkbox"
import { Input } from "@/components/ui/input"
import { useBuilderRegistryActions } from "@/lib/hooks"
import { cn } from "@/lib/utils"

interface ActionMultiselectProps<T extends FieldValues> {
  field: ControllerRenderProps<T>
  searchKeys: (keyof Suggestion)[]
  maxHeight?: string
  className?: string
}

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
        ?.map((action) => ({
          id: action.action,
          label: action.default_title || action.action,
          value: action.action,
          description: action.description,
          group: action.namespace,
          icon: getIcon(action.action, {
            className: "size-6",
          }),
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

  // Group suggestions by namespace for better organization
  const groupedActions = useMemo(() => {
    const groups: Record<string, Suggestion[]> = {}
    filteredSuggestions.forEach((suggestion) => {
      const group = suggestion.group || "Other"
      if (!groups[group]) {
        groups[group] = []
      }
      groups[group].push(suggestion)
    })
    return groups
  }, [filteredSuggestions])

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
            <Search className="absolute left-3 top-1/2 transform -translate-y-1/2 text-gray-400 h-4 w-4" />
            <Input
              placeholder="Search actions..."
              value={searchQuery}
              onChange={(e) => setSearchQuery(e.target.value)}
              className="pl-10"
            />
          </div>
        </div>
        <div className="space-y-4 overflow-y-auto" style={{ maxHeight }}>
          {Object.entries(groupedActions).map(([namespace, actions]) => (
            <div key={namespace} className="space-y-3">
              <div className="flex items-center gap-2">
                <div className="text-xs font-medium capitalize">
                  {namespaceMap[namespace].display_group || namespace}
                </div>
              </div>
              {actions.map((suggestion) => {
                const isSelected = selectedActions.includes(suggestion.value)
                return (
                  <div
                    key={suggestion.value}
                    className={cn(
                      "flex items-start space-x-3 p-3 rounded-lg transition-all duration-200 cursor-pointer hover:bg-gray-50 dark:hover:bg-gray-800",
                      isSelected
                        ? "bg-blue-50 dark:bg-blue-950 border-blue-200 dark:border-blue-800"
                        : "border-gray-200 dark:border-gray-700"
                    )}
                  >
                    <Checkbox
                      checked={isSelected}
                      onCheckedChange={(checked) => {
                        const currentSelections = Array.isArray(field.value)
                          ? field.value
                          : []
                        if (checked === true) {
                          field.onChange([
                            ...currentSelections,
                            suggestion.value,
                          ])
                          return
                        }
                        const nextSelections = currentSelections.filter(
                          (value: string) => value !== suggestion.value
                        )
                        field.onChange(nextSelections)
                      }}
                      onClick={(e) => e.stopPropagation()}
                      className="mt-1 border-input"
                    />
                    <div className="flex-1 min-w-0">
                      <div className="flex items-center space-x-2 mb-1">
                        <div className="size-6">{suggestion.icon}</div>
                        <span className="font-medium text-xs">
                          {suggestion.label}
                        </span>
                      </div>
                      <p className="text-xs text-muted-foreground line-clamp-2">
                        {suggestion.description}
                      </p>
                    </div>
                  </div>
                )
              })}
            </div>
          ))}
          {filteredSuggestions.length === 0 && searchQuery && (
            <div className="text-center py-8">
              <p className="text-gray-500 dark:text-gray-400">
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
              <p className="text-gray-500 dark:text-gray-400">
                No actions available
              </p>
            </div>
          )}
        </div>
      </div>

      <div className="lg:col-span-1 space-y-4">
        <div className="text-sm font-medium">Selected Tools</div>
        <div className="space-y-4">
          {selectedActions.length === 0 ? (
            <p className="text-sm text-gray-500 dark:text-gray-400 text-center py-8">
              No actions selected yet
            </p>
          ) : (
            <div className="space-y-3">
              {selectedActions.map((actionId: string) => {
                const suggestion = suggestions?.find(
                  (s) => s.value === actionId
                )
                return suggestion ? (
                  <div
                    key={actionId}
                    className="flex items-center space-x-2 p-2 bg-gray-50 dark:bg-gray-800 rounded group"
                  >
                    {suggestion.icon}
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
                      <X className="h-3 w-3 text-gray-500 dark:text-gray-400" />
                    </Button>
                  </div>
                ) : null
              })}
              <div className="pt-3 border-t">
                <p className="text-xs text-gray-500 dark:text-gray-400">
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
