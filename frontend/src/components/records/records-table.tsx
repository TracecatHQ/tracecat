"use client"

import { DotsHorizontalIcon } from "@radix-ui/react-icons"
import type { ColumnDef } from "@tanstack/react-table"
import { Trash2 } from "lucide-react"
import { useMemo, useState } from "react"
import type { EntityRead, RecordRead } from "@/client"
import {
  DataTable,
  DataTableColumnHeader,
  type DataTableToolbarProps,
} from "@/components/data-table"
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
import { useDeleteRecord } from "@/lib/hooks"
import { getIconByName } from "@/lib/icons"
import { useWorkspaceId } from "@/providers/workspace-id"

interface RecordsTableProps {
  entityFilter?: string | null
}

export function RecordsTable({ entityFilter }: RecordsTableProps) {
  const workspaceId = useWorkspaceId()
  const { entities } = useEntities(workspaceId)
  const [pageSize, setPageSize] = useState(20)

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

  const { deleteRecord, deleteRecordIsPending } = useDeleteRecord()

  const entityById = useMemo(() => {
    const map = new Map<string, EntityRead>()
    entities?.forEach((entity) => map.set(entity.id, entity))
    return map
  }, [entities])

  const handleDeleteRecord = async (record: RecordRead) => {
    await deleteRecord({
      workspaceId,
      entityId: record.entity_id,
      recordId: record.id,
    })
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
      accessorKey: "data",
      header: ({ column }) => (
        <DataTableColumnHeader
          className="text-xs"
          column={column}
          title="Record Data"
        />
      ),
      cell: ({ row }) => {
        const data = row.original.data || {}
        if (Object.keys(data).length === 0) {
          return <span className="text-xs text-muted-foreground">No data</span>
        }

        return (
          <div className="max-w-md">
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
          {new Date(row.original.created_at).toLocaleDateString()}
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
          {new Date(row.original.updated_at).toLocaleDateString()}
        </div>
      ),
      enableSorting: true,
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
                  onClick={() => handleDeleteRecord(row.original)}
                  disabled={deleteRecordIsPending}
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

  const defaultToolbarProps = useMemo(() => {
    return {
      // No search bar or filter fields
    } as DataTableToolbarProps<RecordRead>
  }, [])

  return (
    <div className="space-y-4">
      <DataTable<RecordRead, unknown>
        data={(records || []) as RecordRead[]}
        columns={columns}
        isLoading={recordsIsLoading || deleteRecordIsPending}
        error={recordsError as Error | null}
        emptyMessage="No records found"
        toolbarProps={defaultToolbarProps}
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
          isLoading: recordsIsLoading || deleteRecordIsPending,
        }}
      />
    </div>
  )
}
