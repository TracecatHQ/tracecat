"use client"

import { ColumnDef } from "@tanstack/react-table"
import { Sparkles } from "lucide-react"

import { type Case } from "@/types/schemas"
import { cn } from "@/lib/utils"
import { Badge } from "@/components/ui/badge"
import { Checkbox } from "@/components/ui/checkbox"
import { StatusBadge } from "@/components/badges"
import { priorities, statuses } from "@/components/cases/data/categories"
import { DataTableColumnHeader } from "@/components/data-table/column-header"
import { LoadingCellState } from "@/components/loading/table"

export const columns: ColumnDef<Case>[] = [
  {
    id: "select",
    header: ({ table }) => (
      <Checkbox
        checked={
          table.getIsAllPageRowsSelected() ||
          (table.getIsSomePageRowsSelected() && "indeterminate")
        }
        onCheckedChange={(value) => table.toggleAllPageRowsSelected(!!value)}
        aria-label="Select all"
        className="translate-y-[2px]"
      />
    ),
    cell: ({ row }) => (
      <Checkbox
        checked={row.getIsSelected()}
        onCheckedChange={(value) => row.toggleSelected(!!value)}
        aria-label="Select row"
        className="translate-y-[2px]"
      />
    ),
    enableSorting: false,
    enableHiding: false,
  },
  {
    accessorKey: "id",
    header: ({ column }) => (
      <DataTableColumnHeader className="text-xs" column={column} title="ID" />
    ),
    cell: ({ row }) => {
      const id = row.getValue<Case["id"]>("id").split(":").pop()
      return <div className="w-[60px] truncate text-xs">#{id}</div>
    },
    enableSorting: true,
    enableHiding: false,
  },
  {
    accessorKey: "status",
    header: ({ column }) => (
      <DataTableColumnHeader column={column} title="Status" />
    ),
    cell: ({ row }) => {
      const status = statuses.find(
        (status) => status.value === row.getValue<Case["status"]>("status")
      )

      if (!status) {
        return null
      }

      return (
        <div className="flex items-center space-x-2">
          {status.icon && (
            <status.icon className="h-3 w-3 text-muted-foreground" />
          )}
          <span className="text-xs">{status.label}</span>
        </div>
      )
    },
    filterFn: (row, id, value) => {
      return value.includes(row.getValue<Case["id"]>(id))
    },
  },
  {
    accessorKey: "priority",
    header: ({ column }) => (
      <DataTableColumnHeader column={column} title="Priority" />
    ),
    cell: ({ row }) => {
      const priority = priorities.find(
        (priority) =>
          priority.value === row.getValue<Case["priority"]>("priority")
      )

      if (!priority) {
        return null
      }

      return (
        <div className="flex items-center space-x-2">
          {priority.icon && (
            <priority.icon className="h-3 w-3 text-muted-foreground" />
          )}
          <span className="text-xs">{priority.label}</span>
        </div>
      )
    },
    filterFn: (row, id, value) => {
      return value.includes(row.getValue<Case["id"]>(id))
    },
  },
  {
    accessorKey: "created_at",
    header: ({ column }) => (
      <DataTableColumnHeader column={column} title="Created At" />
    ),
    cell: ({ row }) => {
      const dt = new Date(row.getValue<Case["created_at"]>("created_at"))
      const strDt = `${dt.toLocaleDateString()}, ${dt.toLocaleTimeString()}`
      return <span className="truncate text-xs">{strDt}</span>
    },
    filterFn: (row, id, value) => {
      return value.includes(row.getValue<Case["id"]>(id))
    },
  },
  {
    accessorKey: "title",
    header: ({ column }) => (
      <DataTableColumnHeader column={column} title="Case Title" />
    ),
    cell: ({ row }) => {
      return (
        <div className="flex space-x-2">
          <span className="max-w-[300px] truncate text-xs">
            {row.getValue<Case["title"]>("title")}
          </span>
        </div>
      )
    },
  },
  {
    accessorKey: "payload",
    header: ({ column }) => (
      <DataTableColumnHeader column={column} title="Payload" />
    ),
    cell: ({ row }) => {
      return (
        <div className="flex space-x-2">
          <span className="max-w-[300px] truncate text-xs text-muted-foreground">
            {JSON.stringify(row.getValue<Case["payload"]>("payload"))}
          </span>
        </div>
      )
    },
  },
  {
    accessorKey: "malice",
    header: ({ column }) => (
      <DataTableColumnHeader column={column} title="Malice" />
    ),
    cell: ({ row }) => {
      const label = row.getValue<Case["malice"]>("malice")
      return (
        <div className="flex space-x-2">
          <span className="max-w-[100px] truncate text-xs text-muted-foreground">
            <StatusBadge status={label}>{label}</StatusBadge>
          </span>
        </div>
      )
    },
    filterFn: (row, id, value) => {
      return value.includes(row.getValue(id))
    },
  },
  {
    accessorKey: "action",
    header: ({ column }) => (
      <DataTableColumnHeader
        column={column}
        title="Action"
        icon={
          <Sparkles className="mr-1 h-3 w-3 animate-pulse fill-yellow-500 text-yellow-500" />
        }
      />
    ),
    cell: ({ row, table }) => {
      const action = row.getValue<Case["action"]>("action")
      if (table.options.meta?.isProcessing && action === null) {
        return <LoadingCellState />
      }
      return (
        <div className="flex space-x-2">
          <span
            className={cn(
              "max-w-[300px] truncate text-xs",
              !action && "text-muted-foreground"
            )}
          >
            {action ? action : "No action available"}
          </span>
        </div>
      )
    },
  },
  {
    accessorKey: "context",
    header: ({ column }) => (
      <DataTableColumnHeader
        column={column}
        title="Context"
        icon={
          <Sparkles className="mr-1 h-3 w-3 animate-pulse fill-yellow-500 text-yellow-500" />
        }
      />
    ),
    cell: ({ row, table }) => {
      const context = row.getValue<Case["context"]>("context")
      if (table.options.meta?.isProcessing && context === null) {
        return <LoadingCellState />
      }
      switch (typeof context) {
        case "object":
          return (
            <div className="flex space-x-2">
              <span className="max-w-[300px] space-x-1 truncate text-xs text-muted-foreground">
                {context
                  ? Object.values(context).map((label, idx) => (
                      <Badge key={idx} variant="outline">
                        {label}
                      </Badge>
                    ))
                  : "No context available"}
              </span>
            </div>
          )
        case "string":
          return (
            <div className="flex space-x-2">
              <span className="max-w-[300px] truncate text-xs text-muted-foreground">
                {context}
              </span>
            </div>
          )
        default:
          return (
            <div className="flex space-x-2">
              <span className="max-w-[300px] truncate text-xs text-muted-foreground">
                {JSON.stringify(context)}
              </span>
            </div>
          )
      }
    },
  },
  {
    accessorKey: "suppression",
    header: ({ column }) => (
      <DataTableColumnHeader column={column} title="Suppressions" />
    ),
    cell: ({ row }) => {
      const maybeSuppressions = row.getValue<Case["suppression"]>("suppression")
      return <span className="text-xs">{maybeSuppressions?.length ?? 0}</span>
    },
  },
  {
    accessorKey: "tags",
    header: ({ column }) => (
      <DataTableColumnHeader
        column={column}
        title="Tags"
        icon={
          <Sparkles className="mr-1 h-3 w-3 animate-pulse fill-yellow-500 text-yellow-500" />
        }
      />
    ),
    cell: ({ row, table }) => {
      const tags = row.getValue<Case["tags"]>("tags")
      if (table.options.meta?.isProcessing && tags === null) {
        return <LoadingCellState />
      }
      return (
        <div className="flex space-x-2">
          <span className="max-w-[300px] space-x-1 truncate text-xs text-muted-foreground">
            {/* tags is null or empty array */}
            {tags && tags.length > 0
              ? tags.map(({ tag, value }, idx) => (
                  <Badge key={idx} variant="outline">
                    {tag}: {value}
                  </Badge>
                ))
              : "No tags"}
          </span>
        </div>
      )
    },
  },
]
