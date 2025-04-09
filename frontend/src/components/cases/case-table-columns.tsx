"use client"

import { CaseReadMinimal } from "@/client"
import { ColumnDef } from "@tanstack/react-table"

import { Checkbox } from "@/components/ui/checkbox"
import { CaseBadge } from "@/components/cases/case-badge"
import {
  PRIORITIES,
  SEVERITIES,
  STATUSES,
} from "@/components/cases/case-categories"
import { DataTableColumnHeader } from "@/components/data-table"

export const columns: ColumnDef<CaseReadMinimal>[] = [
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
        className="translate-y-[2px] border border-muted-foreground/80"
      />
    ),
    cell: ({ row }) => (
      <Checkbox
        onClick={(e) => e.stopPropagation()}
        checked={row.getIsSelected()}
        onCheckedChange={(value) => row.toggleSelected(!!value)}
        aria-label="Select row"
        className="translate-y-[2px] border border-muted-foreground/80"
      />
    ),
    enableSorting: false,
    enableHiding: false,
  },
  {
    accessorKey: "short_id",
    header: ({ column }) => (
      <DataTableColumnHeader className="text-xs" column={column} title="ID" />
    ),
    cell: ({ row }) => (
      <div className="w-[80px] truncate text-xs">
        {row.getValue<CaseReadMinimal["short_id"]>("short_id")}
      </div>
    ),
    enableSorting: true,
    enableHiding: false,
  },
  {
    accessorKey: "summary",
    header: ({ column }) => (
      <DataTableColumnHeader column={column} title="Summary" />
    ),
    cell: ({ row }) => {
      return (
        <div className="flex space-x-2">
          <span className="max-w-[300px] truncate text-xs">
            {row.getValue<CaseReadMinimal["summary"]>("summary")}
          </span>
        </div>
      )
    },
  },
  {
    accessorKey: "status",
    header: ({ column }) => (
      <DataTableColumnHeader column={column} title="Status" />
    ),
    cell: ({ row }) => {
      const status = row.getValue<CaseReadMinimal["status"]>("status")
      const props = STATUSES.find((s) => s.value === status)
      if (!props) {
        return null
      }

      return <CaseBadge {...props} />
    },
    filterFn: (row, id, value) => {
      return value.includes(row.getValue<CaseReadMinimal["id"]>(id))
    },
  },
  {
    accessorKey: "priority",
    header: ({ column }) => (
      <DataTableColumnHeader column={column} title="Priority" />
    ),
    cell: ({ row }) => {
      const priority = row.getValue<CaseReadMinimal["priority"]>("priority")
      if (!priority) {
        return null
      }
      const props = PRIORITIES.find((p) => p.value === priority)!

      return <CaseBadge {...props} />
    },
    filterFn: (row, id, value) => {
      return value.includes(row.getValue<CaseReadMinimal["id"]>(id))
    },
  },
  {
    accessorKey: "severity",
    header: ({ column }) => (
      <DataTableColumnHeader column={column} title="Severity" />
    ),
    cell: ({ row }) => {
      const severity = row.getValue<CaseReadMinimal["severity"]>("severity")
      if (!severity) {
        return null
      }

      const props = SEVERITIES.find((s) => s.value === severity)
      if (!props) {
        return null
      }

      return <CaseBadge {...props} />
    },
    filterFn: (row, id, value) => {
      return value.includes(row.getValue<CaseReadMinimal["id"]>(id))
    },
  },
  {
    accessorKey: "created_at",
    header: ({ column }) => (
      <DataTableColumnHeader column={column} title="Created At" />
    ),
    cell: ({ row }) => {
      const dt = new Date(
        row.getValue<CaseReadMinimal["created_at"]>("created_at")
      )
      const strDt = `${dt.toLocaleDateString()}, ${dt.toLocaleTimeString()}`
      return <span className="truncate text-xs">{strDt}</span>
    },
    filterFn: (row, id, value) => {
      return value.includes(row.getValue<CaseReadMinimal["id"]>(id))
    },
  },
]
