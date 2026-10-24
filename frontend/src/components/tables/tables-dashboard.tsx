"use client"

import { DotsHorizontalIcon } from "@radix-ui/react-icons"
import { ChevronRight, Loader2, Table2Icon } from "lucide-react"
import { useRouter } from "next/navigation"
import { useMemo, useState } from "react"
import type { TableReadMinimal } from "@/client"
import { CatalogHeader } from "@/components/catalog/catalog-header"
import { SqlTypeBadge } from "@/components/data-type/sql-type-display"
import { CenteredSpinner } from "@/components/loading/spinner"
import { AlertNotification } from "@/components/notifications"
import { TableActions } from "@/components/tables/table-actions"
import { DeleteTableDialog } from "@/components/tables/table-delete-dialog"
import { RowTimestamps } from "@/components/timestamps"
import { Button } from "@/components/ui/button"
import {
  Collapsible,
  CollapsibleContent,
  CollapsibleTrigger,
} from "@/components/ui/collapsible"
import {
  DropdownMenu,
  DropdownMenuContent,
  DropdownMenuTrigger,
} from "@/components/ui/dropdown-menu"
import {
  Empty,
  EmptyDescription,
  EmptyHeader,
  EmptyMedia,
  EmptyTitle,
} from "@/components/ui/empty"
import { Item, ItemContent, ItemTitle } from "@/components/ui/item"
import { ScrollArea } from "@/components/ui/scroll-area"
import { useGetTable, useListTables } from "@/lib/hooks"
import { useWorkspaceId } from "@/providers/workspace-id"

function TableColumnsPreview({ tableId }: { tableId: string }) {
  const workspaceId = useWorkspaceId()
  const { table, tableError, tableIsLoading, refetchTable } = useGetTable({
    tableId,
    workspaceId,
  })

  if (tableIsLoading) {
    return (
      <div className="flex items-center gap-2 px-12 py-4 text-xs text-muted-foreground">
        <Loader2 className="size-3.5 animate-spin" />
        Loading columns...
      </div>
    )
  }

  if (tableError) {
    return (
      <div className="flex items-center justify-between gap-3 px-12 py-4">
        <span className="text-xs text-destructive">
          Failed to load columns.
        </span>
        <Button
          variant="outline"
          size="sm"
          className="h-6 px-2.5 text-[11px]"
          onClick={(event) => {
            event.stopPropagation()
            void refetchTable()
          }}
        >
          Retry
        </Button>
      </div>
    )
  }

  if (!table || table.columns.length === 0) {
    return (
      <div className="px-12 py-4 text-xs text-muted-foreground">
        No columns configured.
      </div>
    )
  }

  return (
    <div className="divide-y divide-border/50">
      {table.columns.map((column) => (
        <div
          key={column.id}
          className="flex items-center justify-between gap-3 px-12 py-2.5"
        >
          <span className="min-w-0 truncate text-xs text-foreground">
            {column.name}
          </span>
          <SqlTypeBadge type={column.type} className="shrink-0" />
        </div>
      ))}
    </div>
  )
}

export function TablesDashboard() {
  const router = useRouter()
  const workspaceId = useWorkspaceId()
  const { tables, tablesError, tablesIsLoading } = useListTables({
    workspaceId,
  })
  const [searchQuery, setSearchQuery] = useState("")
  const [expandedTables, setExpandedTables] = useState<Record<string, boolean>>(
    {}
  )
  const [selectedTable, setSelectedTable] = useState<TableReadMinimal | null>(
    null
  )
  const [deleteDialogOpen, setDeleteDialogOpen] = useState(false)

  const allTables = tables ?? []
  const hasActiveSearch = searchQuery.trim().length > 0

  const filteredTables = useMemo(() => {
    const normalizedSearch = searchQuery.trim().toLowerCase()

    return allTables.filter((table) => {
      return (
        normalizedSearch.length === 0 ||
        table.name.toLowerCase().includes(normalizedSearch)
      )
    })
  }, [allTables, searchQuery])

  if (tablesIsLoading) {
    return <CenteredSpinner />
  }

  if (tablesError || !tables) {
    return (
      <AlertNotification
        level="error"
        message={tablesError?.message || "Error loading tables."}
      />
    )
  }

  return (
    <>
      {selectedTable ? (
        <DeleteTableDialog
          table={selectedTable}
          open={deleteDialogOpen}
          onOpenChange={setDeleteDialogOpen}
        />
      ) : null}

      <div className="flex h-full min-h-0 flex-col">
        <CatalogHeader
          searchQuery={searchQuery}
          onSearchChange={setSearchQuery}
          searchPlaceholder="Search tables..."
          displayCount={filteredTables.length}
          countLabel="tables"
        />

        <div className="min-h-0 flex-1 overflow-auto">
          {filteredTables.length === 0 ? (
            <div className="flex h-full p-6">
              <Empty>
                <EmptyHeader>
                  <EmptyMedia variant="icon">
                    <Table2Icon className="size-5 text-muted-foreground/60" />
                  </EmptyMedia>
                  <EmptyTitle>
                    {hasActiveSearch ? "No tables found" : "No tables yet"}
                  </EmptyTitle>
                  <EmptyDescription>
                    {hasActiveSearch
                      ? "No tables found matching your criteria."
                      : "Create a table to store lookup data for workflows."}
                  </EmptyDescription>
                </EmptyHeader>
              </Empty>
            </div>
          ) : (
            <ScrollArea className="h-full [&>[data-radix-scroll-area-viewport]]:[scrollbar-width:none] [&>[data-radix-scroll-area-viewport]::-webkit-scrollbar]:hidden [&>[data-orientation=vertical]]:!hidden [&>[data-orientation=horizontal]]:!hidden">
              <div className="w-full pb-10">
                {filteredTables.map((table) => {
                  const isExpanded = expandedTables[table.id] ?? false
                  const tableHref = `/workspaces/${workspaceId}/tables/${table.id}`

                  function openTable() {
                    router.push(tableHref)
                  }

                  return (
                    <Collapsible
                      key={table.id}
                      open={isExpanded}
                      onOpenChange={(nextOpen) =>
                        setExpandedTables((prev) => ({
                          ...prev,
                          [table.id]: nextOpen,
                        }))
                      }
                    >
                      <div className="border-b border-border/50">
                        <div className="flex items-center gap-3 px-3 py-1.5 transition-colors hover:bg-muted/50">
                          <CollapsibleTrigger asChild>
                            <button
                              type="button"
                              className="flex h-7 w-7 shrink-0 items-center justify-center [&[data-state=open]_.chevron]:rotate-90"
                              aria-label={
                                isExpanded
                                  ? `Collapse ${table.name} columns`
                                  : `Expand ${table.name} columns`
                              }
                            >
                              <ChevronRight className="chevron size-4 text-muted-foreground transition-transform duration-200" />
                            </button>
                          </CollapsibleTrigger>

                          <div
                            role="link"
                            tabIndex={0}
                            className="flex min-w-0 flex-1 cursor-pointer items-center gap-3 text-left outline-none focus-visible:ring-2 focus-visible:ring-ring focus-visible:ring-offset-2"
                            onClick={openTable}
                            onKeyDown={(event) => {
                              if (event.key === "Enter" || event.key === " ") {
                                event.preventDefault()
                                openTable()
                              }
                            }}
                          >
                            <Item className="w-full flex-nowrap rounded-none border-none px-0 py-0">
                              <ItemContent className="min-w-0 gap-0">
                                <ItemTitle className="min-w-0 truncate text-xs">
                                  {table.name}
                                </ItemTitle>
                              </ItemContent>
                            </Item>

                            <RowTimestamps
                              className="ml-auto"
                              createdAt={table.created_at}
                              updatedAt={table.updated_at}
                            />
                          </div>

                          <div className="flex shrink-0 items-center gap-2">
                            <DropdownMenu>
                              <DropdownMenuTrigger asChild>
                                <Button
                                  variant="ghost"
                                  size="sm"
                                  className="size-7 p-0"
                                  onClick={(event) => event.stopPropagation()}
                                >
                                  <span className="sr-only">Open menu</span>
                                  <DotsHorizontalIcon className="size-4" />
                                </Button>
                              </DropdownMenuTrigger>
                              <DropdownMenuContent align="end">
                                <TableActions
                                  table={table}
                                  onDeleteClick={(nextTable) => {
                                    setSelectedTable(nextTable)
                                    setDeleteDialogOpen(true)
                                  }}
                                />
                              </DropdownMenuContent>
                            </DropdownMenu>
                          </div>
                        </div>

                        <CollapsibleContent>
                          {isExpanded ? (
                            <TableColumnsPreview tableId={table.id} />
                          ) : null}
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
    </>
  )
}
