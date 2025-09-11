"use client"

import { DotsHorizontalIcon } from "@radix-ui/react-icons"
import type { ColumnDef } from "@tanstack/react-table"
import { formatDistanceToNow } from "date-fns"
import { Pencil, Trash2, Unlink } from "lucide-react"
import { useMemo, useState } from "react"
import type { CaseRecordRead, EntityRead } from "@/client"
import { DataTable, DataTableColumnHeader } from "@/components/data-table"
import { CompactJsonViewer } from "@/components/json-viewer-compact"
import { Avatar, AvatarFallback } from "@/components/ui/avatar"
import { Button } from "@/components/ui/button"
import {
  DropdownMenu,
  DropdownMenuContent,
  DropdownMenuItem,
  DropdownMenuTrigger,
} from "@/components/ui/dropdown-menu"
import { useEntities } from "@/hooks/use-entities"
import { getIconByName } from "@/lib/icons"
import { capitalizeFirst } from "@/lib/utils"
import { DeleteCaseRecordAlertDialog } from "./delete-case-record-dialog"
import { EditCaseRecordDialog } from "./edit-case-record-dialog"

interface CaseRecordsTableProps {
  records: CaseRecordRead[]
  isLoading: boolean
  error: Error | null
  caseId: string
  workspaceId: string
}

export function CaseRecordsTable({
  records,
  isLoading,
  error,
  caseId,
  workspaceId,
}: CaseRecordsTableProps) {
  const { entities } = useEntities(workspaceId)
  const [selectedRecord, setSelectedRecord] = useState<CaseRecordRead | null>(
    null
  )
  const [editDialogOpen, setEditDialogOpen] = useState(false)
  const [deleteDialogOpen, setDeleteDialogOpen] = useState(false)
  const [unlinkDialogOpen, setUnlinkDialogOpen] = useState(false)

  const entityById = useMemo(() => {
    const map = new Map<string, EntityRead>()
    entities?.forEach((entity) => map.set(entity.id, entity))
    return map
  }, [entities])

  const handleEditRecord = (record: CaseRecordRead) => {
    setSelectedRecord(record)
    setEditDialogOpen(true)
  }

  const handleUnlinkRecord = (record: CaseRecordRead) => {
    setSelectedRecord(record)
    setUnlinkDialogOpen(true)
  }

  const handleDeleteRecord = (record: CaseRecordRead) => {
    setSelectedRecord(record)
    setDeleteDialogOpen(true)
  }

  const columns: ColumnDef<CaseRecordRead>[] = [
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
                {row.original.entity_display_name ||
                  entity?.display_name ||
                  row.original.entity_id}
              </div>
              <div className="text-xs text-muted-foreground">
                {row.original.entity_key ||
                  entity?.key ||
                  row.original.entity_id}
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
          {capitalizeFirst(
            formatDistanceToNow(new Date(row.original.created_at), {
              addSuffix: true,
            })
          )}
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
          {capitalizeFirst(
            formatDistanceToNow(new Date(row.original.updated_at), {
              addSuffix: true,
            })
          )}
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
        return (
          <div className="max-w-xs">
            <CompactJsonViewer src={data} />
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
                  onClick={() => handleUnlinkRecord(row.original)}
                >
                  <Unlink className="mr-2 h-3 w-3" />
                  <span>Unlink from case</span>
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
    <>
      <DataTable<CaseRecordRead, unknown>
        data={records}
        columns={columns}
        isLoading={isLoading}
        error={error}
        emptyMessage="No records linked to this case"
        tableId={`${workspaceId}-case-${caseId}-records`}
      />
      <EditCaseRecordDialog
        open={editDialogOpen}
        onOpenChange={setEditDialogOpen}
        record={selectedRecord}
        caseId={caseId}
        workspaceId={workspaceId}
      />
      <DeleteCaseRecordAlertDialog
        open={unlinkDialogOpen}
        onOpenChange={setUnlinkDialogOpen}
        record={selectedRecord}
        caseId={caseId}
        workspaceId={workspaceId}
        mode="unlink"
      />
      <DeleteCaseRecordAlertDialog
        open={deleteDialogOpen}
        onOpenChange={setDeleteDialogOpen}
        record={selectedRecord}
        caseId={caseId}
        workspaceId={workspaceId}
        mode="delete"
      />
    </>
  )
}
