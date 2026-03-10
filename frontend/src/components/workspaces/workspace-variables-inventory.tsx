"use client"

import { BracketsIcon, ChevronRight } from "lucide-react"
import { useMemo, useState } from "react"
import { stringify } from "yaml"
import type { VariableReadMinimal } from "@/client"
import {
  CatalogHeader,
  type CatalogHeaderSelectFilter,
} from "@/components/catalog/catalog-header"
import { CenteredSpinner } from "@/components/loading/spinner"
import { AlertNotification } from "@/components/notifications"
import { Badge } from "@/components/ui/badge"
import { Button } from "@/components/ui/button"
import {
  Collapsible,
  CollapsibleContent,
  CollapsibleTrigger,
} from "@/components/ui/collapsible"
import {
  Empty,
  EmptyDescription,
  EmptyHeader,
  EmptyMedia,
  EmptyTitle,
} from "@/components/ui/empty"
import { Item, ItemActions, ItemContent, ItemTitle } from "@/components/ui/item"
import { ScrollArea } from "@/components/ui/scroll-area"
import {
  DeleteVariableAlertDialog,
  DeleteVariableAlertDialogTrigger,
} from "@/components/workspaces/delete-workspace-variable"
import {
  EditVariableDialog,
  EditVariableDialogTrigger,
} from "@/components/workspaces/edit-workspace-variable"
import {
  buildVariableGroups,
  normalizeVariableEnvironment,
} from "@/components/workspaces/variables-utils"
import { useWorkspaceVariables } from "@/lib/hooks"
import { useWorkspaceId } from "@/providers/workspace-id"

function stringifyVariableValue(value: unknown): string {
  if (typeof value === "string") {
    return value
  }
  if (
    typeof value === "number" ||
    typeof value === "boolean" ||
    value === null ||
    value === undefined
  ) {
    return String(value)
  }

  try {
    return stringify(value).trimEnd()
  } catch {
    return String(value)
  }
}

export function WorkspaceVariablesInventory() {
  const workspaceId = useWorkspaceId()
  const { variables, variablesIsLoading, variablesError } =
    useWorkspaceVariables(workspaceId)
  const [searchQuery, setSearchQuery] = useState("")
  const [environmentFilter, setEnvironmentFilter] = useState("all")
  const [expandedGroups, setExpandedGroups] = useState<Record<string, boolean>>(
    {}
  )
  const [selectedVariable, setSelectedVariable] =
    useState<VariableReadMinimal | null>(null)

  const allVariables = variables ?? []
  const hasActiveFilters =
    searchQuery.trim().length > 0 || environmentFilter !== "all"

  const availableEnvironments = useMemo(
    () =>
      Array.from(
        new Set(
          allVariables.map((variable) =>
            normalizeVariableEnvironment(variable.environment)
          )
        )
      ).sort((a, b) => a.localeCompare(b)),
    [allVariables]
  )

  const filteredGroups = useMemo(() => {
    const normalizedSearch = searchQuery.trim().toLowerCase()

    return buildVariableGroups(
      allVariables.filter((variable) => {
        const matchesSearch =
          normalizedSearch.length === 0 ||
          variable.name.toLowerCase().includes(normalizedSearch)
        const matchesEnvironment =
          environmentFilter === "all"
            ? true
            : normalizeVariableEnvironment(variable.environment) ===
              environmentFilter

        return matchesSearch && matchesEnvironment
      })
    )
  }, [allVariables, environmentFilter, searchQuery])

  const selectFilters: CatalogHeaderSelectFilter[] = [
    {
      key: "environment",
      value: environmentFilter,
      onValueChange: setEnvironmentFilter,
      placeholder: "Environment",
      allValue: "all",
      widthClassName: "w-[190px]",
      options: [
        { value: "all", label: "All environments" },
        ...availableEnvironments.map((environment) => ({
          value: environment,
          label: environment,
        })),
      ],
    },
  ]

  if (variablesIsLoading) {
    return <CenteredSpinner />
  }

  if (variablesError || !variables) {
    return (
      <AlertNotification
        level="error"
        message={`Error loading variables: ${variablesError?.message || "Variables undefined"}`}
      />
    )
  }

  return (
    <DeleteVariableAlertDialog
      selectedVariable={selectedVariable}
      setSelectedVariable={setSelectedVariable}
    >
      <EditVariableDialog
        selectedVariable={selectedVariable}
        setSelectedVariable={setSelectedVariable}
      >
        <div className="flex h-full min-h-0 flex-col">
          <CatalogHeader
            searchQuery={searchQuery}
            onSearchChange={setSearchQuery}
            searchPlaceholder="Search variables..."
            selectFilters={selectFilters}
            displayCount={filteredGroups.length}
            countLabel="variables"
          />

          <div className="min-h-0 flex-1 overflow-auto">
            {filteredGroups.length === 0 ? (
              <div className="flex h-full p-6">
                <Empty>
                  <EmptyHeader>
                    <EmptyMedia variant="icon">
                      <BracketsIcon className="size-5 text-muted-foreground/60" />
                    </EmptyMedia>
                    <EmptyTitle>
                      {hasActiveFilters
                        ? "No variables found"
                        : "No variables yet"}
                    </EmptyTitle>
                    <EmptyDescription>
                      {hasActiveFilters
                        ? "No variables found matching your criteria."
                        : "Create a variable to store reusable values for workflows."}
                    </EmptyDescription>
                  </EmptyHeader>
                </Empty>
              </div>
            ) : (
              <ScrollArea className="h-full [&>[data-radix-scroll-area-viewport]]:[scrollbar-width:none] [&>[data-radix-scroll-area-viewport]::-webkit-scrollbar]:hidden [&>[data-orientation=vertical]]:!hidden [&>[data-orientation=horizontal]]:!hidden">
                <div className="w-full pb-10">
                  {filteredGroups.map((group) => {
                    const isExpanded = expandedGroups[group.name] ?? false

                    return (
                      <Collapsible
                        key={group.name}
                        open={isExpanded}
                        onOpenChange={(nextOpen) =>
                          setExpandedGroups((prev) => ({
                            ...prev,
                            [group.name]: nextOpen,
                          }))
                        }
                      >
                        <div className="border-b border-border/50">
                          <div className="flex items-center gap-2 px-3 py-1.5 transition-colors hover:bg-muted/50">
                            <CollapsibleTrigger asChild>
                              <button
                                type="button"
                                className="flex min-w-0 flex-1 items-center gap-2 text-left [&[data-state=open]_.chevron]:rotate-90"
                              >
                                <div className="flex h-7 w-7 shrink-0 items-center justify-center">
                                  <ChevronRight className="chevron size-4 text-muted-foreground transition-transform duration-200" />
                                </div>
                                <Item className="w-full flex-nowrap rounded-none border-none px-0 py-0">
                                  <ItemContent className="min-w-0 gap-0">
                                    <ItemTitle className="min-w-0 truncate text-xs">
                                      {group.name}
                                    </ItemTitle>
                                    <div className="flex flex-wrap items-center gap-2 text-[11px] text-muted-foreground">
                                      <span>
                                        {group.environments.length}{" "}
                                        environment(s)
                                      </span>
                                    </div>
                                  </ItemContent>
                                </Item>
                              </button>
                            </CollapsibleTrigger>
                          </div>

                          <CollapsibleContent>
                            <div className="divide-y divide-border/50">
                              {group.items.map((variable) => (
                                <div
                                  key={variable.id}
                                  className="px-3 py-1.5 pl-12"
                                >
                                  <Item
                                    variant="default"
                                    size="sm"
                                    className="w-full flex-nowrap rounded-none border-none px-0 py-0 text-left"
                                  >
                                    <ItemContent className="min-w-0 gap-2">
                                      <div className="flex items-start gap-3">
                                        <div className="min-w-0 flex-1 space-y-1">
                                          <div className="flex flex-wrap items-center gap-2">
                                            <Badge
                                              variant="secondary"
                                              className="h-5 px-2 text-[10px] font-normal"
                                            >
                                              {normalizeVariableEnvironment(
                                                variable.environment
                                              )}
                                            </Badge>
                                          </div>
                                          {variable.description ? (
                                            <p className="text-xs text-muted-foreground">
                                              {variable.description}
                                            </p>
                                          ) : null}
                                        </div>
                                        <ItemActions className="ml-auto flex shrink-0 items-center gap-1.5">
                                          <EditVariableDialogTrigger asChild>
                                            <Button
                                              variant="outline"
                                              size="sm"
                                              className="h-6 border-input bg-white px-2.5 text-[11px] text-foreground hover:bg-muted"
                                              onClick={(event) => {
                                                event.stopPropagation()
                                                setSelectedVariable(variable)
                                              }}
                                            >
                                              Edit
                                            </Button>
                                          </EditVariableDialogTrigger>
                                          <DeleteVariableAlertDialogTrigger
                                            asChild
                                          >
                                            <Button
                                              variant="outline"
                                              size="sm"
                                              className="h-6 border-input bg-white px-2.5 text-[11px] text-foreground hover:border-destructive hover:bg-destructive hover:text-destructive-foreground"
                                              onClick={(event) => {
                                                event.stopPropagation()
                                                setSelectedVariable(variable)
                                              }}
                                            >
                                              Delete
                                            </Button>
                                          </DeleteVariableAlertDialogTrigger>
                                        </ItemActions>
                                      </div>

                                      <div className="space-y-2">
                                        {Object.entries(variable.values)
                                          .length > 0 ? (
                                          Object.entries(variable.values).map(
                                            ([key, value]) => {
                                              const displayValue =
                                                stringifyVariableValue(value)
                                              const isMultiline =
                                                displayValue.includes("\n") ||
                                                displayValue.length > 80

                                              return (
                                                <div
                                                  key={`${variable.id}-${key}`}
                                                  className="rounded-md border border-border/60 bg-muted/20 px-3 py-2"
                                                >
                                                  <div className="text-[10px] font-medium uppercase tracking-wide text-muted-foreground">
                                                    {key}
                                                  </div>
                                                  {isMultiline ? (
                                                    <pre className="mt-1 overflow-x-auto whitespace-pre-wrap break-words font-mono text-xs text-foreground">
                                                      {displayValue}
                                                    </pre>
                                                  ) : (
                                                    <div className="mt-1 font-mono text-xs text-foreground">
                                                      {displayValue}
                                                    </div>
                                                  )}
                                                </div>
                                              )
                                            }
                                          )
                                        ) : (
                                          <span className="text-xs text-muted-foreground">
                                            No values configured.
                                          </span>
                                        )}
                                      </div>
                                    </ItemContent>
                                  </Item>
                                </div>
                              ))}
                            </div>
                          </CollapsibleContent>
                        </div>
                      </Collapsible>
                    )
                  })}
                </div>
              </ScrollArea>
            )}
          </div>
        </div>
      </EditVariableDialog>
    </DeleteVariableAlertDialog>
  )
}
