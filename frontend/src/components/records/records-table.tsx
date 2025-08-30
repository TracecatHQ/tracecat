"use client"

import { DotsHorizontalIcon } from "@radix-ui/react-icons"
import type { ColumnDef } from "@tanstack/react-table"
import { Pencil, Trash2 } from "lucide-react"
import { useMemo, useState } from "react"
import type { EntityRead, RecordRead } from "@/client"
import { DataTable, DataTableColumnHeader } from "@/components/data-table"
import { JsonViewWithControls } from "@/components/json-viewer"
import { Avatar, AvatarFallback } from "@/components/ui/avatar"
import { Button } from "@/components/ui/button"
import {
  DropdownMenu,
  DropdownMenuContent,
  DropdownMenuItem,
  DropdownMenuTrigger,
} from "@/components/ui/dropdown-menu"
import { useRecordsPagination } from "@/hooks/pagination/use-records-pagination"
import { useEntities } from "@/hooks/use-entities"
import { getIconByName } from "@/lib/icons"
import { capitalizeFirst, shortTimeAgo } from "@/lib/utils"
import { useWorkspaceId } from "@/providers/workspace-id"
import { DeleteRecordAlertDialog } from "./delete-record-dialog"
import { EditRecordDialog } from "./edit-record-dialog"

interface RecordsTableProps {
  entityFilter?: string | null
}

export function RecordsTable({ entityFilter }: RecordsTableProps) {
  const workspaceId = useWorkspaceId()
  const { entities } = useEntities(workspaceId)
  const [pageSize, setPageSize] = useState(20)
  const [selectedRecord, setSelectedRecord] = useState<RecordRead | null>(null)
  const [editDialogOpen, setEditDialogOpen] = useState(false)
  const [deleteDialogOpen, setDeleteDialogOpen] = useState(false)

  const {
    data: records,
    isLoading: recordsIsLoading,
    error: recordsError,
    goToNextPage,
    goToPreviousPage,
    goToFirstPage,
    hasNextPage,
    hasPreviousPage,
    currentPage,
    totalEstimate,
    startItem,
    endItem,
  } = useRecordsPagination({
    workspaceId,
    limit: pageSize,
    entityId: entityFilter,
  })

  const entityById = useMemo(() => {
    const map = new Map<string, EntityRead>()
    entities?.forEach((entity) => map.set(entity.id, entity))
    return map
  }, [entities])

  const handleEditRecord = (record: RecordRead) => {
    setSelectedRecord(record)
    setEditDialogOpen(true)
  }

  const handleDeleteRecord = (record: RecordRead) => {
    setSelectedRecord(record)
    setDeleteDialogOpen(true)
  }

  const columns: ColumnDef<RecordRead>[] = [
    {
      accessorKey: "entity_id",
      header: ({ column }) => (
        <DataTableColumnHeader
          className="text-xs"
          column={column}
          title="Entity"
        />
      ),
      cell: ({ row }) => {
        const entity = entityById.get(row.original.entity_id)
        const IconComponent = entity?.icon
          ? getIconByName(entity.icon)
          : undefined
        const initials = entity?.display_name?.[0]?.toUpperCase() || "?"

        return (
          <div className="flex items-center gap-2.5">
            <Avatar className="size-7 shrink-0">
              <AvatarFallback className="text-xs">
                {IconComponent ? (
                  <IconComponent className="size-4" />
                ) : (
                  initials
                )}
              </AvatarFallback>
            </Avatar>
            <div>
              <div className="text-sm font-medium">
                {entity?.display_name || row.original.entity_id}
              </div>
              <div className="text-xs text-muted-foreground">
                {entity?.key || row.original.entity_id}
              </div>
            </div>
          </div>
        )
      },
      enableSorting: false,
      enableHiding: false,
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
          {capitalizeFirst(shortTimeAgo(new Date(row.original.created_at)))}
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
          {capitalizeFirst(shortTimeAgo(new Date(row.original.updated_at)))}
        </div>
      ),
      enableSorting: true,
      enableHiding: false,
    },
    {
      accessorKey: "data",
      header: ({ column }) => (
        <DataTableColumnHeader
          className="text-xs"
          column={column}
          title="Record"
        />
      ),
      cell: ({ row }) => {
        const data = row.original.data || {}
        if (Object.keys(data).length === 0) {
          return <span className="text-xs text-muted-foreground">No data</span>
        }

        return (
          <div className="w-64">
            <JsonViewWithControls
              src={data}
              defaultExpanded={false}
              defaultTab="nested"
              showControls={false}
            />
          </div>
        )
      },
      enableSorting: false,
      enableHiding: false,
    },
    {
      id: "actions",
      enableHiding: false,
      cell: ({ row }) => {
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
                <DropdownMenuItem
                  onClick={() => handleEditRecord(row.original)}
                >
                  <Pencil className="mr-2 h-3 w-3" />
                  <span>Edit</span>
                </DropdownMenuItem>
                <DropdownMenuItem
                  onClick={() => handleDeleteRecord(row.original)}
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
      <DataTable<RecordRead, unknown>
        data={(records || []) as RecordRead[]}
        columns={columns}
        isLoading={recordsIsLoading}
        error={recordsError as Error | null}
        emptyMessage="No records found"
        tableId={`${workspaceId}-records`}
        serverSidePagination={{
          currentPage,
          hasNextPage,
          hasPreviousPage,
          pageSize,
          totalEstimate,
          startItem,
          endItem,
          onNextPage: goToNextPage,
          onPreviousPage: goToPreviousPage,
          onFirstPage: goToFirstPage,
          onPageSizeChange: setPageSize,
          isLoading: recordsIsLoading,
        }}
      />
      <EditRecordDialog
        open={editDialogOpen}
        onOpenChange={setEditDialogOpen}
        record={selectedRecord}
      />
      <DeleteRecordAlertDialog
        open={deleteDialogOpen}
        onOpenChange={setDeleteDialogOpen}
        record={selectedRecord}
      />
    </div>
  )
}
