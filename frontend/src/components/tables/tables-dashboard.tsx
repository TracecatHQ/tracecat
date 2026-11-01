"use client"

import { DotsHorizontalIcon } from "@radix-ui/react-icons"
import {
  ChevronRight,
  Copy,
  FolderIcon,
  FolderKanban,
  Loader2,
  Pencil,
  Table2Icon,
  Trash2,
} from "lucide-react"
import { useRouter, useSearchParams } from "next/navigation"
import { useMemo, useState } from "react"
import type { TableFolderDirectoryItem, TableReadMinimal } from "@/client"
import { CatalogHeader } from "@/components/catalog/catalog-header"
import { SqlTypeBadge } from "@/components/data-type/sql-type-display"
import { FolderNotEmptyDialog } from "@/components/folder-not-empty-dialog"
import { CenteredSpinner } from "@/components/loading/spinner"
import { AlertNotification } from "@/components/notifications"
import { TableActions } from "@/components/tables/table-actions"
import { DeleteTableDialog } from "@/components/tables/table-delete-dialog"
import {
  normalizeTableFolderPath,
  TableFolderMoveDialog,
  TableFolderRenameDialog,
  TableMoveDialog,
} from "@/components/tables/table-folder-dialogs"
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
  DropdownMenuGroup,
  DropdownMenuItem,
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
import { toast } from "@/components/ui/use-toast"
import {
  useTableDirectoryItems,
  useTableFolders,
} from "@/hooks/use-table-folders"
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

type FolderDialog = {
  kind: "rename" | "move" | "not-empty"
  folder: TableFolderDirectoryItem
} | null

function folderPathHref(workspaceId: string, path: string) {
  const base = `/workspaces/${workspaceId}/tables`
  return path === "/" ? base : `${base}?path=${encodeURIComponent(path)}`
}

function TableFolderBreadcrumb({
  path,
  onNavigate,
}: {
  path: string
  onNavigate: (path: string) => void
}) {
  const segments = path.split("/").filter(Boolean)
  return (
    <nav
      aria-label="Folder path"
      className="flex items-center gap-1 border-b border-border/50 px-4 py-2 text-xs text-muted-foreground"
    >
      <button
        type="button"
        className="hover:text-foreground"
        onClick={() => onNavigate("/")}
      >
        Tables
      </button>
      {segments.map((segment, index) => {
        const segmentPath = `/${segments.slice(0, index + 1).join("/")}/`
        const isLast = index === segments.length - 1
        return (
          <span key={segmentPath} className="flex items-center gap-1">
            <ChevronRight className="size-3" />
            {isLast ? (
              <span className="text-foreground">{segment}</span>
            ) : (
              <button
                type="button"
                className="hover:text-foreground"
                onClick={() => onNavigate(segmentPath)}
              >
                {segment}
              </button>
            )}
          </span>
        )
      })}
    </nav>
  )
}

function TableFolderRow({
  folder,
  onOpen,
  onDialog,
}: {
  folder: TableFolderDirectoryItem
  onOpen: (path: string) => void
  onDialog: (dialog: FolderDialog) => void
}) {
  const workspaceId = useWorkspaceId()
  const { deleteFolder } = useTableFolders(workspaceId, { enabled: false })
  return (
    <div className="border-b border-border/50">
      <div className="flex items-center gap-3 px-3 py-1.5 transition-colors hover:bg-muted/50">
        <div className="flex h-7 w-7 shrink-0 items-center justify-center">
          <FolderIcon className="size-4 text-muted-foreground" />
        </div>
        <button
          type="button"
          className="flex min-w-0 flex-1 items-center gap-3 text-left outline-none focus-visible:ring-2 focus-visible:ring-ring"
          onClick={() => onOpen(folder.path)}
        >
          <span className="min-w-0 truncate text-xs font-medium">
            {folder.name}
          </span>
          <span className="ml-auto text-xs text-muted-foreground">
            {folder.num_items} {folder.num_items === 1 ? "item" : "items"}
          </span>
        </button>
        <DropdownMenu>
          <DropdownMenuTrigger asChild>
            <Button variant="ghost" size="sm" className="size-7 p-0">
              <span className="sr-only">Open folder menu</span>
              <DotsHorizontalIcon className="size-4" />
            </Button>
          </DropdownMenuTrigger>
          <DropdownMenuContent align="end">
            <DropdownMenuGroup>
              <DropdownMenuItem
                className="text-xs"
                onClick={() => {
                  navigator.clipboard.writeText(folder.id)
                  toast({ title: "Copied", description: "Folder ID copied" })
                }}
              >
                <Copy className="mr-2 h-3 w-3" />
                Copy folder ID
              </DropdownMenuItem>
              <DropdownMenuItem
                className="text-xs"
                onClick={() => onDialog({ kind: "rename", folder })}
              >
                <Pencil className="mr-2 h-3 w-3" />
                Rename folder
              </DropdownMenuItem>
              <DropdownMenuItem
                className="text-xs"
                onClick={() => onDialog({ kind: "move", folder })}
              >
                <FolderKanban className="mr-2 h-3 w-3" />
                Move folder
              </DropdownMenuItem>
              <DropdownMenuItem
                className="text-xs text-destructive focus:text-destructive"
                onClick={() => {
                  if (folder.num_items > 0) {
                    onDialog({ kind: "not-empty", folder })
                    return
                  }
                  void deleteFolder({ folderId: folder.id }).catch(() => {
                    // The mutation hook reports failures.
                  })
                }}
              >
                <Trash2 className="mr-2 h-3 w-3" />
                Delete folder
              </DropdownMenuItem>
            </DropdownMenuGroup>
          </DropdownMenuContent>
        </DropdownMenu>
      </div>
    </div>
  )
}

function TableRow({
  table,
  isExpanded,
  onExpandedChange,
  onDeleteClick,
  onMoveClick,
}: {
  table: TableReadMinimal
  isExpanded: boolean
  onExpandedChange: (open: boolean) => void
  onDeleteClick: (table: TableReadMinimal) => void
  onMoveClick: (table: TableReadMinimal) => void
}) {
  const router = useRouter()
  const workspaceId = useWorkspaceId()
  const tableHref = `/workspaces/${workspaceId}/tables/${table.id}`

  function openTable() {
    router.push(tableHref)
  }

  return (
    <Collapsible open={isExpanded} onOpenChange={onExpandedChange}>
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
                  onDeleteClick={onDeleteClick}
                  onMoveClick={onMoveClick}
                />
              </DropdownMenuContent>
            </DropdownMenu>
          </div>
        </div>

        <CollapsibleContent>
          {isExpanded ? <TableColumnsPreview tableId={table.id} /> : null}
        </CollapsibleContent>
      </div>
    </Collapsible>
  )
}

export function TablesDashboard() {
  const router = useRouter()
  const searchParams = useSearchParams()
  const workspaceId = useWorkspaceId()
  const currentPath = normalizeTableFolderPath(
    searchParams?.get("path") ?? null
  )
  const { tables, tablesError, tablesIsLoading } = useListTables({
    workspaceId,
  })
  const [searchQuery, setSearchQuery] = useState("")
  const hasActiveSearch = searchQuery.trim().length > 0
  const { directoryItems, directoryItemsError, directoryItemsIsLoading } =
    useTableDirectoryItems(currentPath, workspaceId, {
      enabled: !hasActiveSearch,
    })
  const [expandedTables, setExpandedTables] = useState<Record<string, boolean>>(
    {}
  )
  const [selectedTable, setSelectedTable] = useState<TableReadMinimal | null>(
    null
  )
  const [deleteDialogOpen, setDeleteDialogOpen] = useState(false)
  const [tableToMove, setTableToMove] = useState<TableReadMinimal | null>(null)
  const [folderDialog, setFolderDialog] = useState<FolderDialog>(null)

  const tablesById = useMemo(
    () => new Map((tables ?? []).map((table) => [table.id, table])),
    [tables]
  )

  const searchResults = useMemo(() => {
    const normalizedSearch = searchQuery.trim().toLowerCase()
    return (tables ?? []).filter((table) =>
      table.name.toLowerCase().includes(normalizedSearch)
    )
  }, [tables, searchQuery])

  const folderItems = hasActiveSearch
    ? []
    : (directoryItems ?? []).filter(
        (item): item is TableFolderDirectoryItem => item.type === "folder"
      )
  const tableItems: TableReadMinimal[] = hasActiveSearch
    ? searchResults
    : (directoryItems ?? []).flatMap((item) => {
        if (item.type !== "table") return []
        return [tablesById.get(item.id) ?? item]
      })

  const navigateToFolder = (path: string) => {
    setSearchQuery("")
    router.push(folderPathHref(workspaceId, path))
  }

  const isLoading =
    tablesIsLoading || (!hasActiveSearch && directoryItemsIsLoading)
  if (isLoading) {
    return <CenteredSpinner />
  }

  const error = tablesError ?? (hasActiveSearch ? null : directoryItemsError)
  if (error || !tables) {
    return (
      <AlertNotification
        level="error"
        message={error?.message || "Error loading tables."}
      />
    )
  }

  const isEmpty = folderItems.length === 0 && tableItems.length === 0

  return (
    <>
      {selectedTable ? (
        <DeleteTableDialog
          table={selectedTable}
          open={deleteDialogOpen}
          onOpenChange={setDeleteDialogOpen}
        />
      ) : null}
      {tableToMove ? (
        <TableMoveDialog
          open
          onOpenChange={(open) => {
            if (!open) setTableToMove(null)
          }}
          table={tableToMove}
        />
      ) : null}
      {folderDialog?.kind === "rename" ? (
        <TableFolderRenameDialog
          open
          onOpenChange={(open) => {
            if (!open) setFolderDialog(null)
          }}
          folder={folderDialog.folder}
        />
      ) : null}
      {folderDialog?.kind === "move" ? (
        <TableFolderMoveDialog
          open
          onOpenChange={(open) => {
            if (!open) setFolderDialog(null)
          }}
          folder={folderDialog.folder}
        />
      ) : null}
      {folderDialog?.kind === "not-empty" ? (
        <FolderNotEmptyDialog
          open
          onOpenChange={(open) => {
            if (!open) setFolderDialog(null)
          }}
          folderId={folderDialog.folder.id}
          folderName={folderDialog.folder.name}
          numItems={folderDialog.folder.num_items}
          itemNoun="tables or folders"
        />
      ) : null}

      <div className="flex h-full min-h-0 flex-col">
        <CatalogHeader
          searchQuery={searchQuery}
          onSearchChange={setSearchQuery}
          searchPlaceholder="Search tables..."
          displayCount={tableItems.length}
          countLabel="tables"
        />
        {!hasActiveSearch && currentPath !== "/" ? (
          <TableFolderBreadcrumb
            path={currentPath}
            onNavigate={navigateToFolder}
          />
        ) : null}

        <div className="min-h-0 flex-1 overflow-auto">
          {isEmpty ? (
            <div className="flex h-full p-6">
              <Empty>
                <EmptyHeader>
                  <EmptyMedia variant="icon">
                    <Table2Icon className="size-5 text-muted-foreground/60" />
                  </EmptyMedia>
                  <EmptyTitle>
                    {hasActiveSearch
                      ? "No tables found"
                      : currentPath === "/"
                        ? "No tables yet"
                        : "This folder is empty"}
                  </EmptyTitle>
                  <EmptyDescription>
                    {hasActiveSearch
                      ? "No tables found matching your criteria."
                      : currentPath === "/"
                        ? "Create a table to store lookup data for workflows."
                        : "Move tables here or create a subfolder."}
                  </EmptyDescription>
                </EmptyHeader>
              </Empty>
            </div>
          ) : (
            <ScrollArea className="h-full [&>[data-radix-scroll-area-viewport]]:[scrollbar-width:none] [&>[data-radix-scroll-area-viewport]::-webkit-scrollbar]:hidden [&>[data-orientation=vertical]]:!hidden [&>[data-orientation=horizontal]]:!hidden">
              <div className="w-full pb-10">
                {folderItems.map((folder) => (
                  <TableFolderRow
                    key={folder.id}
                    folder={folder}
                    onOpen={navigateToFolder}
                    onDialog={setFolderDialog}
                  />
                ))}
                {tableItems.map((table) => (
                  <TableRow
                    key={table.id}
                    table={table}
                    isExpanded={expandedTables[table.id] ?? false}
                    onExpandedChange={(nextOpen) =>
                      setExpandedTables((prev) => ({
                        ...prev,
                        [table.id]: nextOpen,
                      }))
                    }
                    onDeleteClick={(nextTable) => {
                      setSelectedTable(nextTable)
                      setDeleteDialogOpen(true)
                    }}
                    onMoveClick={setTableToMove}
                  />
                ))}
              </div>
            </ScrollArea>
          )}
        </div>
      </div>
    </>
  )
}
