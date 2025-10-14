"use client"

import { DotsHorizontalIcon } from "@radix-ui/react-icons"
import type { ColumnDef } from "@tanstack/react-table"
import { format } from "date-fns"
import fuzzysort from "fuzzysort"
import type { ReactNode } from "react"
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
} from "@/components/cases/case-panel-selectors"
import { DataTableColumnHeader } from "@/components/data-table"
import { TagBadge } from "@/components/tag-badge"
import { Badge } from "@/components/ui/badge"
import { Button } from "@/components/ui/button"
import { Checkbox } from "@/components/ui/checkbox"
import {
  DropdownMenu,
  DropdownMenuContent,
  DropdownMenuTrigger,
} from "@/components/ui/dropdown-menu"
import {
  Tooltip,
  TooltipContent,
  TooltipTrigger,
} from "@/components/ui/tooltip"
import { User } from "@/lib/auth"
import { capitalizeFirst, shortTimeAgo } from "@/lib/utils"

export function createColumns(
  setSelectedCase: (case_: CaseReadMinimal) => void
): ColumnDef<CaseReadMinimal>[] {
  return [
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
        <div
          onClick={(e) => {
            e.stopPropagation()
            e.preventDefault()
          }}
        >
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
      cell: ({ row }) => {
        const assignee = row.original.assignee

        return (
          <div className="flex w-[120px] flex-col gap-1 text-xs">
            <span className="truncate font-medium">
              {row.getValue<CaseReadMinimal["short_id"]>("short_id")}
            </span>
            {assignee ? (
              <AssignedUser user={new User(assignee)} className="text-xs" />
            ) : (
              <NoAssignee text="Not assigned" className="text-xs" />
            )}
          </div>
        )
      },
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
        const summary = row.getValue<CaseReadMinimal["summary"]>("summary")
        const updatedAt = row.original.updated_at
        const tags = row.original.tags

        let updatedBadge: ReactNode = null
        if (updatedAt) {
          const dt = new Date(updatedAt)
          const shortTime = capitalizeFirst(shortTimeAgo(dt))
          const fullDateTime = format(dt, "PPpp")
          updatedBadge = (
            <Tooltip>
              <TooltipTrigger asChild>
                <Badge
                  variant="secondary"
                  className="w-fit text-[10px] font-medium capitalize"
                >
                  {shortTime}
                </Badge>
              </TooltipTrigger>
              <TooltipContent>
                <p>{fullDateTime}</p>
              </TooltipContent>
            </Tooltip>
          )
        }

        return (
          <div className="flex max-w-[320px] flex-col gap-2 text-xs">
            {updatedBadge}
            <span className="truncate text-xs">{summary}</span>
            {tags?.length ? (
              <div className="flex flex-wrap gap-1">
                {tags.map((tag) => (
                  <TagBadge key={tag.id} tag={tag} />
                ))}
              </div>
            ) : null}
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
        const shortTime = capitalizeFirst(shortTimeAgo(dt))
        const fullDateTime = format(dt, "PPpp") // e.g. "Apr 13, 2024, 2:30 PM EDT"

        return (
          <Tooltip>
            <TooltipTrigger>
              <span className="truncate text-xs">{shortTime}</span>
            </TooltipTrigger>
            <TooltipContent>
              <p>{fullDateTime}</p>
            </TooltipContent>
          </Tooltip>
        )
      },
      filterFn: (row, id, value) => {
        const dateStr =
          row.getValue<CaseReadMinimal["created_at"]>("created_at")
        return value.includes(dateStr)
      },
    },
    {
      id: "actions",
      header: ({ column }) => (
        <DataTableColumnHeader column={column} title="" />
      ),
      enableHiding: false,
      enableSorting: false,
      cell: ({ row }) => {
        // Import is done dynamically to avoid circular dependency issues
        const { CaseActions } = require("@/components/cases/case-actions")

        return (
          <DropdownMenu>
            <DropdownMenuTrigger asChild>
              <Button
                variant="ghost"
                className="size-6 p-0"
                onClick={(e) => e.stopPropagation()}
              >
                <span className="sr-only">Open menu</span>
                <DotsHorizontalIcon className="size-4" />
              </Button>
            </DropdownMenuTrigger>
            <DropdownMenuContent>
              <CaseActions
                item={row.original}
                setSelectedCase={setSelectedCase}
              />
            </DropdownMenuContent>
          </DropdownMenu>
        )
      },
    },
  ]
}
