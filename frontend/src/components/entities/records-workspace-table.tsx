"use client"

import { DotsHorizontalIcon } from "@radix-ui/react-icons"
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query"
import type { ColumnDef } from "@tanstack/react-table"
import { Archive, RefreshCcw, Trash2 } from "lucide-react"
import { useEffect, useMemo, useState } from "react"
import JsonView from "react18-json-view"
import {
  type EntitiesListAllRecordsResponse,
  entitiesArchiveRecord,
  entitiesDeleteRecord,
  entitiesListAllRecords,
  entitiesRestoreRecord,
  type RecordRead,
} from "@/client"
import { DataTable, DataTableColumnHeader } from "@/components/data-table"
import {
  AlertDialog,
  AlertDialogAction,
  AlertDialogCancel,
  AlertDialogContent,
  AlertDialogDescription,
  AlertDialogFooter,
  AlertDialogHeader,
  AlertDialogTitle,
} from "@/components/ui/alert-dialog"
import { Avatar, AvatarFallback } from "@/components/ui/avatar"
import { Badge } from "@/components/ui/badge"
import { Button } from "@/components/ui/button"
import {
  DropdownMenu,
  DropdownMenuContent,
  DropdownMenuItem,
  DropdownMenuSeparator,
  DropdownMenuTrigger,
} from "@/components/ui/dropdown-menu"
import { Label } from "@/components/ui/label"
import {
  Select,
  SelectContent,
  SelectItem,
  SelectTrigger,
  SelectValue,
} from "@/components/ui/select"
import { toast } from "@/components/ui/use-toast"
import { useEntities } from "@/lib/hooks/use-entities"
import { getIconByName } from "@/lib/icon-data"
import { shortTimeAgo } from "@/lib/utils"
import { useWorkspace } from "@/providers/workspace"

import "react18-json-view/src/style.css"

interface RecordsWorkspaceTableProps {
  includeDeleted?: boolean
}

export function RecordsWorkspaceTable({
  includeDeleted = false,
}: RecordsWorkspaceTableProps = {}) {
  const { workspaceId } = useWorkspace()
  const queryClient = useQueryClient()
  const { entities } = useEntities(workspaceId || "", true)

  // Filters and pagination state
  const [entityFilter, setEntityFilter] = useState<string | null>(null)
  const [pageSize, setPageSize] = useState(20)
  const [cursor, setCursor] = useState<string | null>(null)
  const [pageIndex, setPageIndex] = useState(0)

  // Maintain a stack of previous cursors to support back navigation
  const [prevStack, setPrevStack] = useState<string[]>([])

  useEffect(() => {
    // Reset pagination when filters change
    setCursor(null)
    setPrevStack([])
    setPageIndex(0)
  }, [entityFilter, includeDeleted, pageSize])

  const entityById = useMemo(() => {
    const map = new Map<string, { display_name: string; icon?: string }>()
    ;(entities || []).forEach((e) =>
      map.set(e.id, { display_name: e.display_name, icon: e.icon || undefined })
    )
    return map
  }, [entities])

  const { data, isLoading, error } = useQuery<EntitiesListAllRecordsResponse>({
    queryKey: [
      "workspace-records-cursor",
      workspaceId,
      entityFilter,
      includeDeleted,
      pageSize,
      cursor,
    ],
    queryFn: async () => {
      if (!workspaceId)
        return {
          items: [],
          next_cursor: null,
          prev_cursor: null,
          has_more: false,
          has_previous: false,
          total_estimate: null,
        } as EntitiesListAllRecordsResponse
      return entitiesListAllRecords({
        workspaceId,
        entityId: entityFilter || undefined,
        includeDeleted: includeDeleted || undefined,
        limit: pageSize,
        cursor: cursor || undefined,
      })
    },
    enabled: !!workspaceId,
    placeholderData: (previousData) => previousData,
  })

  const records = data?.items || []
  const nextCursor = data?.next_cursor || null
  const hasNextPage = !!data?.has_more
  const hasPreviousPage = pageIndex > 0 || !!data?.has_previous

  const startItem = pageIndex * pageSize + (records.length > 0 ? 1 : 0)
  const endItem = startItem + records.length - 1

  const onNextPage = () => {
    if (!hasNextPage || !nextCursor) return
    if (cursor) setPrevStack((s) => [...s, cursor])
    setCursor(nextCursor)
    setPageIndex((i) => i + 1)
  }
  const onPreviousPage = () => {
    if (pageIndex === 0) return
    const prev = prevStack[prevStack.length - 1] || null
    setPrevStack((s) => s.slice(0, -1))
    setCursor(prev)
    setPageIndex((i) => Math.max(0, i - 1))
  }
  const onFirstPage = () => {
    setPrevStack([])
    setCursor(null)
    setPageIndex(0)
  }

  const { mutateAsync: doArchive } = useMutation({
    mutationFn: async (recordId: string) =>
      entitiesArchiveRecord({ workspaceId: workspaceId!, recordId }),
    onSuccess: () => {
      queryClient.invalidateQueries({
        queryKey: [
          "workspace-records-cursor",
          workspaceId,
          entityFilter,
          includeDeleted,
          pageSize,
          cursor,
        ],
      })
      toast({ title: "Record archived" })
    },
    onError: () =>
      toast({ title: "Failed to archive", variant: "destructive" }),
  })

  const { mutateAsync: doRestore } = useMutation({
    mutationFn: async (recordId: string) =>
      entitiesRestoreRecord({ workspaceId: workspaceId!, recordId }),
    onSuccess: () => {
      queryClient.invalidateQueries({
        queryKey: [
          "workspace-records-cursor",
          workspaceId,
          entityFilter,
          includeDeleted,
          pageSize,
          cursor,
        ],
      })
      toast({ title: "Record restored" })
    },
    onError: () =>
      toast({ title: "Failed to restore", variant: "destructive" }),
  })

  const { mutateAsync: doDelete } = useMutation({
    mutationFn: async (recordId: string) =>
      entitiesDeleteRecord({ workspaceId: workspaceId!, recordId }),
    onSuccess: () => {
      queryClient.invalidateQueries({
        queryKey: [
          "workspace-records-cursor",
          workspaceId,
          entityFilter,
          includeDeleted,
          pageSize,
          cursor,
        ],
      })
      toast({ title: "Record deleted" })
    },
    onError: () => toast({ title: "Failed to delete", variant: "destructive" }),
  })

  const [confirmAction, setConfirmAction] = useState<null | {
    type: "archive" | "restore" | "delete"
    record: RecordRead
  }>(null)

  const columns: ColumnDef<RecordRead, unknown>[] = [
    {
      id: "entity",
      accessorFn: (row) => row.entity_id,
      header: ({ column }) => (
        <DataTableColumnHeader
          className="text-xs"
          column={column}
          title="Entity"
        />
      ),
      cell: ({ row }) => {
        const entity = entityById.get(row.original.entity_id)
        const IconComp = entity?.icon ? getIconByName(entity.icon) : undefined
        const initials = entity?.display_name?.[0]?.toUpperCase() || "?"
        return (
          <div className="flex items-center gap-2">
            <Avatar className="size-6 shrink-0">
              <AvatarFallback className="text-xs">
                {IconComp ? <IconComp className="size-3" /> : initials}
              </AvatarFallback>
            </Avatar>
            <div className="text-xs">
              {entity?.display_name || row.original.entity_id}
            </div>
          </div>
        )
      },
      enableSorting: false,
      enableHiding: false,
    },
    {
      id: "record",
      accessorFn: (row) => row.field_data,
      header: ({ column }) => (
        <DataTableColumnHeader
          className="text-xs"
          column={column}
          title="Record"
        />
      ),
      cell: ({ row }) => {
        const fieldData = row.original.field_data || {}
        if (Object.keys(fieldData).length === 0) {
          return (
            <span className="text-xs text-muted-foreground">No fields</span>
          )
        }

        return (
          <div className="overflow-auto w-64">
            <JsonView
              collapsed={false}
              displaySize
              enableClipboard
              src={fieldData}
              className="break-all text-xs"
              theme="atom"
              collapseStringsAfterLength={50}
            />
          </div>
        )
      },
      enableSorting: false,
      enableHiding: false,
    },
    {
      id: "status",
      accessorFn: (row) => (row.deleted_at ? "archived" : "active"),
      header: ({ column }) => (
        <DataTableColumnHeader
          className="text-xs"
          column={column}
          title="Status"
        />
      ),
      cell: ({ row }) => (
        <Badge
          variant={row.original.deleted_at ? "secondary" : "default"}
          className="text-xs"
        >
          {row.original.deleted_at ? "Archived" : "Active"}
        </Badge>
      ),
      filterFn: (row, _id, value: string[]) => {
        const status = row.original.deleted_at ? "archived" : "active"
        return value.includes(status)
      },
      enableSorting: false,
      enableHiding: false,
      enableColumnFilter: true,
    },
    {
      accessorKey: "created_at",
      header: ({ column }) => (
        <DataTableColumnHeader
          className="text-xs"
          column={column}
          title="Created"
        />
      ),
      cell: ({ row }) => (
        <div className="text-xs text-muted-foreground">
          {shortTimeAgo(new Date(row.original.created_at))}
        </div>
      ),
      enableSorting: true,
      enableHiding: false,
    },
    {
      accessorKey: "updated_at",
      header: ({ column }) => (
        <DataTableColumnHeader
          className="text-xs"
          column={column}
          title="Updated"
        />
      ),
      cell: ({ row }) => (
        <div className="text-xs text-muted-foreground">
          {shortTimeAgo(new Date(row.original.updated_at))}
        </div>
      ),
      enableSorting: true,
      enableHiding: false,
    },
    {
      id: "actions",
      enableHiding: false,
      cell: ({ row }) => {
        const archived = !!row.original.deleted_at
        return (
          <div className="flex justify-end">
            <DropdownMenu>
              <DropdownMenuTrigger asChild>
                <Button
                  variant="ghost"
                  className="size-8 p-0"
                  onClick={(e) => e.stopPropagation()}
                >
                  <span className="sr-only">Open menu</span>
                  <DotsHorizontalIcon className="size-4" />
                </Button>
              </DropdownMenuTrigger>
              <DropdownMenuContent align="end">
                {!archived ? (
                  <DropdownMenuItem
                    onClick={() =>
                      setConfirmAction({
                        type: "archive",
                        record: row.original,
                      })
                    }
                  >
                    <Archive className="mr-2 h-3 w-3" /> Archive
                  </DropdownMenuItem>
                ) : (
                  <DropdownMenuItem
                    onClick={() =>
                      setConfirmAction({
                        type: "restore",
                        record: row.original,
                      })
                    }
                  >
                    <RefreshCcw className="mr-2 h-3 w-3" /> Restore
                  </DropdownMenuItem>
                )}
                <DropdownMenuSeparator />
                <DropdownMenuItem
                  onClick={() =>
                    setConfirmAction({ type: "delete", record: row.original })
                  }
                >
                  <Trash2 className="mr-2 h-3 w-3 text-rose-600" />
                  <span className="text-rose-600">Delete</span>
                </DropdownMenuItem>
              </DropdownMenuContent>
            </DropdownMenu>
          </div>
        )
      },
    },
  ]

  return (
    <div className="space-y-4">
      <div className="flex items-center gap-3">
        <div className="flex items-center gap-2">
          <Label
            htmlFor="record-filter-entity"
            className="text-xs text-muted-foreground"
          >
            Entity
          </Label>
          <Select
            value={entityFilter || "all"}
            onValueChange={(v) => setEntityFilter(v === "all" ? null : v)}
          >
            <SelectTrigger id="record-filter-entity" className="h-7 w-[180px]">
              <SelectValue placeholder="All entities" />
            </SelectTrigger>
            <SelectContent>
              <SelectItem value="all">All entities</SelectItem>
              {(entities || []).map((e) => (
                <SelectItem key={e.id} value={e.id}>
                  {e.display_name}
                </SelectItem>
              ))}
            </SelectContent>
          </Select>
        </div>
      </div>

      <DataTable
        columns={columns}
        data={records}
        isLoading={isLoading}
        error={error as Error | null}
        emptyMessage="No records found"
        tableId="workspace-records"
        serverSidePagination={{
          currentPage: pageIndex,
          hasNextPage,
          hasPreviousPage,
          pageSize,
          totalEstimate: data?.total_estimate ?? undefined,
          startItem,
          endItem,
          onNextPage,
          onPreviousPage,
          onFirstPage,
          onPageSizeChange: (ps) => setPageSize(ps),
          isLoading,
        }}
      />

      <AlertDialog
        open={!!confirmAction}
        onOpenChange={(open) => !open && setConfirmAction(null)}
      >
        <AlertDialogContent>
          <AlertDialogHeader>
            <AlertDialogTitle>
              {confirmAction?.type === "delete"
                ? "Delete record?"
                : confirmAction?.type === "archive"
                  ? "Archive record?"
                  : "Restore record?"}
            </AlertDialogTitle>
            <AlertDialogDescription>
              {confirmAction?.type === "delete"
                ? "This will permanently delete the record. This action cannot be undone."
                : confirmAction?.type === "archive"
                  ? "Archived records are hidden by default and can be restored later."
                  : "Restores an archived record to active state."}
            </AlertDialogDescription>
          </AlertDialogHeader>
          <AlertDialogFooter>
            <AlertDialogCancel>Cancel</AlertDialogCancel>
            <AlertDialogAction
              onClick={async () => {
                if (!confirmAction) return
                const id = confirmAction.record.id
                try {
                  if (confirmAction.type === "delete") await doDelete(id)
                  if (confirmAction.type === "archive") await doArchive(id)
                  if (confirmAction.type === "restore") await doRestore(id)
                } finally {
                  setConfirmAction(null)
                }
              }}
            >
              {confirmAction?.type === "delete"
                ? "Delete"
                : confirmAction?.type === "archive"
                  ? "Archive"
                  : "Restore"}
            </AlertDialogAction>
          </AlertDialogFooter>
        </AlertDialogContent>
      </AlertDialog>
    </div>
  )
}
