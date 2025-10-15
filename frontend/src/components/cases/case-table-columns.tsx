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
        <div className="flex w-full justify-center">
          <Checkbox
            className="border-foreground/50"
            checked={
              table.getIsAllPageRowsSelected() ||
              (table.getIsSomePageRowsSelected() && "indeterminate")
            }
            onCheckedChange={(value) =>
              table.toggleAllPageRowsSelected(!!value)
            }
            aria-label="Select all"
          />
        </div>
      ),
      cell: ({ row }) => (
        <div
          className="flex w-full justify-center"
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
      meta: {
        headerClassName: "w-12 min-w-[3rem] max-w-[3rem] px-2 text-center",
        cellClassName: "w-12 min-w-[3rem] max-w-[3rem] px-2 text-center",
      },
    },

    {
      accessorKey: "short_id",
      header: ({ column }) => (
        <DataTableColumnHeader className="text-xs" column={column} title="ID" />
      ),
      cell: ({ row }) => {
        const assignee = row.original.assignee

        return (
          <div className="flex w-[148px] min-w-[148px] max-w-[148px] flex-col gap-[0.4rem] text-xs">
            <span className="truncate text-xs font-medium text-muted-foreground">
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
      meta: {
        headerClassName: "w-[148px] min-w-[148px] max-w-[148px] pr-8 text-left",
        cellClassName: "w-[148px] min-w-[148px] max-w-[148px] pr-8 text-left",
        headerStyle: { width: "148px" },
        cellStyle: { width: "148px" },
      },
    },
    {
      accessorKey: "summary",
      header: ({ column }) => (
        <DataTableColumnHeader column={column} title="Summary" />
      ),
      cell: ({ row }) => {
        const summary = row.getValue<CaseReadMinimal["summary"]>("summary")
        const tags = row.original.tags
        const priority = row.original.priority
        const severity = row.original.severity

        const priorityProps = priority ? PRIORITIES[priority] : undefined
        const severityProps = severity ? SEVERITIES[severity] : undefined

        const metadataItems: ReactNode[] = []

        if (priorityProps) {
          metadataItems.push(
            <CaseBadge
              key="priority"
              {...priorityProps}
              className="font-medium"
            />
          )
        }

        if (severityProps) {
          metadataItems.push(
            <CaseBadge
              key="severity"
              {...severityProps}
              className="font-medium"
            />
          )
        }

        if (tags?.length) {
          tags.forEach((tag) => {
            metadataItems.push(
              <TagBadge key={tag.id} tag={tag} className="font-medium" />
            )
          })
        }

        return (
          <div className="flex min-w-[24rem] flex-1 flex-col gap-[0.45rem] text-xs">
            <span className="truncate text-xs font-medium">{summary}</span>
            {metadataItems.length ? (
              <div className="flex flex-wrap items-center gap-1 text-xs">
                {metadataItems}
              </div>
            ) : null}
          </div>
        )
      },
      filterFn: (row, id, value) => {
        const rowValue = String(row.getValue<CaseReadMinimal["summary"]>(id))
        return fuzzysort.single(String(value), rowValue) !== null
      },
      meta: {
        headerClassName: "min-w-[24rem] max-w-none text-left",
        cellClassName: "min-w-[24rem] max-w-none text-left",
      },
    },
    {
      accessorKey: "status",
      header: ({ column }) => (
        <DataTableColumnHeader
          column={column}
          title="Status"
          className="justify-end"
          buttonClassName="ml-auto h-8 justify-end px-0 data-[state=open]:bg-accent"
        />
      ),
      cell: ({ row }) => {
        const status = row.getValue<CaseReadMinimal["status"]>("status")
        const props = STATUSES[status]
        if (!props) {
          return null
        }

        return (
          <div className="flex w-full justify-end">
            <CaseBadge {...props} className="font-medium" />
          </div>
        )
      },
      filterFn: (row, id, value) => {
        return value.includes(row.getValue<CaseReadMinimal["status"]>("status"))
      },
      meta: {
        headerClassName:
          "w-[136px] min-w-[136px] max-w-[136px] px-0 text-right",
        cellClassName: "w-[136px] min-w-[136px] max-w-[136px] px-0 text-right",
        headerStyle: { width: "136px" },
        cellStyle: { width: "136px" },
      },
    },
    {
      accessorKey: "created_at",
      header: ({ column }) => (
        <DataTableColumnHeader
          column={column}
          title="Created"
          className="justify-end"
          buttonClassName="ml-auto h-8 justify-end px-0 data-[state=open]:bg-accent"
        />
      ),
      cell: ({ row }) => {
        const dt = new Date(
          row.getValue<CaseReadMinimal["created_at"]>("created_at")
        )
        const shortTime = capitalizeFirst(shortTimeAgo(dt))
        const fullDateTime = format(dt, "PPpp") // e.g. "Apr 13, 2024, 2:30 PM EDT"

        return (
          <Tooltip>
            <TooltipTrigger className="flex w-full justify-end">
              <span className="block truncate text-right text-xs">
                {shortTime}
              </span>
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
      meta: {
        headerClassName:
          "w-[96px] min-w-[96px] max-w-[96px] justify-end px-0 text-right",
        cellClassName:
          "w-[96px] min-w-[96px] max-w-[96px] px-0 pl-2 text-right",
        headerStyle: { width: "96px" },
        cellStyle: { width: "96px" },
      },
    },
    {
      accessorKey: "updated_at",
      header: ({ column }) => (
        <DataTableColumnHeader
          column={column}
          title="Updated"
          className="justify-end"
          buttonClassName="ml-auto h-8 justify-end px-0 data-[state=open]:bg-accent"
        />
      ),
      cell: ({ row }) => {
        const updatedAt =
          row.getValue<CaseReadMinimal["updated_at"]>("updated_at")
        if (!updatedAt) {
          return <span className="text-xs text-muted-foreground">—</span>
        }

        const dt = new Date(updatedAt)
        const shortTime = capitalizeFirst(shortTimeAgo(dt))
        const fullDateTime = format(dt, "PPpp")

        return (
          <Tooltip>
            <TooltipTrigger className="flex w-full justify-end">
              <span className="block truncate text-right text-xs">
                {shortTime}
              </span>
            </TooltipTrigger>
            <TooltipContent>
              <p>{fullDateTime}</p>
            </TooltipContent>
          </Tooltip>
        )
      },
      filterFn: (row, id, value) => {
        const dateStr = row.getValue<CaseReadMinimal["updated_at"]>(id)
        return value.includes(dateStr)
      },
      meta: {
        headerClassName:
          "w-[96px] min-w-[96px] max-w-[96px] justify-end px-0 text-right",
        cellClassName:
          "w-[96px] min-w-[96px] max-w-[96px] px-0 pl-2 text-right",
        headerStyle: { width: "96px" },
        cellStyle: { width: "96px" },
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
      meta: {
        headerClassName: "w-14 min-w-[3.5rem] max-w-[3.5rem] px-0 text-right",
        cellClassName: "w-14 min-w-[3.5rem] max-w-[3.5rem] px-0 text-right",
        headerStyle: { width: "56px" },
        cellStyle: { width: "56px" },
      },
    },
  ]
}
