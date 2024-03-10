"use client"

import { ColumnDef } from "@tanstack/react-table"

import { Checkbox } from "@/components/ui/checkbox"
import { DataTableColumnHeader } from "@/components/data-table/column-header"

import { Event } from "./data/schema"

export const columns: ColumnDef<Event>[] = [
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
    enableHiding: true,
  },
  {
    accessorKey: "published_at",
    header: ({ column }) => (
      <DataTableColumnHeader
        className="text-xs"
        column={column}
        title="published_at"
      />
    ),
    cell: ({ row }) => (
      <div className="w-[100px] text-xs">{row.getValue("published_at")}</div>
    ),
    enableSorting: true,
    enableHiding: true,
  },
  {
    accessorKey: "workflow_run_id",
    header: ({ column }) => (
      <DataTableColumnHeader
        className="text-xs"
        column={column}
        title="workflow_run_id"
      />
    ),
    cell: ({ row }) => (
      <div className="w-[50px] text-xs">{row.getValue("workflow_run_id")}</div>
    ),
    enableSorting: true,
    enableHiding: true,
  },
  {
    accessorKey: "action_title",
    header: ({ column }) => (
      <DataTableColumnHeader
        className="text-xs"
        column={column}
        title="action_title"
      />
    ),
    cell: ({ row }) => (
      <div className="w-[150px] max-w-[200px] text-xs">
        {row.getValue("action_title")}
      </div>
    ),
    enableSorting: true,
    enableHiding: true,
  },
  {
    accessorKey: "trail",
    header: ({ column }) => (
      <DataTableColumnHeader column={column} title="trail" />
    ),
    cell: ({ row }) => {
      return (
        <div className="flex space-x-2">
          <span className="max-w-[400px] truncate text-xs text-muted-foreground">
            {row.getValue("trail")}
          </span>
        </div>
      )
    },
  },
]
