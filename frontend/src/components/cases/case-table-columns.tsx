"use client"

import type { ColumnDef } from "@tanstack/react-table"
import { format, formatDistanceToNow } from "date-fns"
import fuzzysort from "fuzzysort"
import type { CaseReadMinimal } from "@/client"
import { CaseBadge } from "@/components/cases/case-badge"
import {
  PRIORITIES,
  SEVERITIES,
  STATUSES,
} from "@/components/cases/case-categories"
import {
  AssignedUser,
  NoAssignee,
  UNASSIGNED,
} from "@/components/cases/case-panel-selectors"
import { DataTableColumnHeader } from "@/components/data-table"
import { Checkbox } from "@/components/ui/checkbox"
import {
  Tooltip,
  TooltipContent,
  TooltipTrigger,
} from "@/components/ui/tooltip"
import { User } from "@/lib/auth"

export const columns: ColumnDef<CaseReadMinimal>[] = [
  {
    id: "select",
    header: ({ table }) => (
      <Checkbox
        className="border-foreground/50"
        checked={
          table.getIsAllPageRowsSelected() ||
          (table.getIsSomePageRowsSelected() && "indeterminate")
        }
        onCheckedChange={(value) => table.toggleAllPageRowsSelected(!!value)}
        aria-label="Select all"
      />
    ),
    cell: ({ row }) => (
      <div onClick={(e) => e.stopPropagation()}>
        <Checkbox
          className="border-foreground/50"
          checked={row.getIsSelected()}
          onCheckedChange={(value) => row.toggleSelected(!!value)}
          aria-label="Select row"
        />
      </div>
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
    filterFn: (row, id, value) => {
      return value.includes(row.getValue<CaseReadMinimal["short_id"]>(id))
    },
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
    filterFn: (row, id, value) => {
      const rowValue = String(row.getValue<CaseReadMinimal["summary"]>(id))
      return fuzzysort.single(String(value), rowValue) !== null
    },
  },
  {
    accessorKey: "status",
    header: ({ column }) => (
      <DataTableColumnHeader column={column} title="Status" />
    ),
    cell: ({ row }) => {
      const status = row.getValue<CaseReadMinimal["status"]>("status")
      const props = STATUSES[status]
      if (!props) {
        return null
      }

      return <CaseBadge {...props} />
    },
    filterFn: (row, id, value) => {
      return value.includes(row.getValue<CaseReadMinimal["status"]>("status"))
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
      const props = PRIORITIES[priority]

      return <CaseBadge {...props} />
    },
    filterFn: (row, id, value) => {
      return value.includes(
        row.getValue<CaseReadMinimal["priority"]>("priority")
      )
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

      const props = SEVERITIES[severity]
      if (!props) {
        return null
      }

      return <CaseBadge {...props} />
    },
    filterFn: (row, id, value) => {
      return value.includes(
        row.getValue<CaseReadMinimal["severity"]>("severity")
      )
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
      const timeAgo = formatDistanceToNow(dt, { addSuffix: true })
      const fullDateTime = format(dt, "PPpp") // e.g. "Apr 13, 2024, 2:30 PM EDT"

      return (
        <Tooltip>
          <TooltipTrigger>
            <span className="truncate text-xs">{fullDateTime}</span>
          </TooltipTrigger>
          <TooltipContent>
            <p>{timeAgo}</p>
          </TooltipContent>
        </Tooltip>
      )
    },
    filterFn: (row, id, value) => {
      const dateStr = row.getValue<CaseReadMinimal["created_at"]>("created_at")
      return value.includes(dateStr)
    },
  },
  {
    accessorKey: "updated_at",
    header: ({ column }) => (
      <DataTableColumnHeader column={column} title="Updated At" />
    ),
    cell: ({ row }) => {
      const dt = new Date(
        row.getValue<CaseReadMinimal["updated_at"]>("updated_at")
      )
      const timeAgo = formatDistanceToNow(dt, { addSuffix: true })
      const fullDateTime = format(dt, "PPpp") // e.g. "Apr 13, 2024, 2:30 PM EDT"

      return (
        <Tooltip>
          <TooltipTrigger>
            <span className="truncate text-xs">{fullDateTime}</span>
          </TooltipTrigger>
          <TooltipContent>
            <p>{timeAgo}</p>
          </TooltipContent>
        </Tooltip>
      )
    },
    filterFn: (row, id, value) => {
      const dateStr = row.getValue<CaseReadMinimal["updated_at"]>("updated_at")
      return value.includes(dateStr)
    },
  },
  {
    id: "Assignee",
    accessorKey: "assignee",
    header: ({ column }) => (
      <DataTableColumnHeader column={column} title="Assignee" />
    ),
    cell: ({ getValue }) => {
      const user = getValue<CaseReadMinimal["assignee"]>()
      if (!user) {
        return <NoAssignee text="Not assigned" className="text-xs" />
      }

      return <AssignedUser user={new User(user)} className="text-xs" />
    },
    filterFn: (row, id, value) => {
      const assignee = row.getValue<CaseReadMinimal["assignee"]>("assignee")
      if (!assignee) {
        // Handle unassigned case
        return value.includes(UNASSIGNED)
      }
      const user = new User(assignee)
      const displayName = user.getDisplayName()
      return value.includes(displayName)
    },
  },
]
