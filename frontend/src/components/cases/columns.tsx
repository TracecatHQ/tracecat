"use client"

import { ColumnDef } from "@tanstack/react-table"

import { cn } from "@/lib/utils"
import { Badge } from "@/components/ui/badge"
import { Checkbox } from "@/components/ui/checkbox"
import { StatusBadge } from "@/components/badges"
import {
  indicators,
  priorities,
  statuses,
} from "@/components/cases/data/categories"
import { DataTableColumnHeader } from "@/components/data-table/column-header"

import { Case } from "./data/schema"

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
    cell: ({ row }) => (
      <div className="w-[60px] text-xs">&#x23; {row.getValue("id")}</div>
    ),
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
        (status) => status.value === row.getValue("status")
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
      return value.includes(row.getValue(id))
    },
  },
  {
    accessorKey: "priority",
    header: ({ column }) => (
      <DataTableColumnHeader column={column} title="Priority" />
    ),
    cell: ({ row }) => {
      const priority = priorities.find(
        (priority) => priority.value === row.getValue("priority")
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
      return value.includes(row.getValue(id))
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
            {row.getValue("title")}
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
            {row.getValue("payload")}
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
      const label = row.getValue("malice") as string
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
      <DataTableColumnHeader column={column} title="Action" />
    ),
    cell: ({ row }) => {
      return (
        <div className="flex space-x-2">
          <span className="max-w-[300px] truncate text-xs">
            {row.getValue("action")}
          </span>
        </div>
      )
    },
  },
  {
    accessorKey: "context",
    header: ({ column }) => (
      <DataTableColumnHeader column={column} title="Context" />
    ),
    cell: ({ row }) => {
      const context = row.getValue("context") as Record<string, string>
      return (
        <div className="flex space-x-2">
          <span className="max-w-[300px] space-x-1 truncate text-xs text-muted-foreground">
            {Object.values(context).map((label, idx) => (
              <Badge key={idx} variant="outline">
                {label}
              </Badge>
            ))}
          </span>
        </div>
      )
    },
  },
  {
    accessorKey: "suppression",
    header: ({ column }) => (
      <DataTableColumnHeader column={column} title="Suppression" />
    ),
    cell: ({ row }) => {
      return (
        <div className="flex space-x-2">
          <span className="max-w-[300px] truncate text-xs text-muted-foreground">
            {row.getValue("suppression")}
          </span>
        </div>
      )
    },
  },
]
