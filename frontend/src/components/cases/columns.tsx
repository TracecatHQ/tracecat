"use client"

import { CaseRead } from "@/client"
import { ColumnDef } from "@tanstack/react-table"

import { cn } from "@/lib/utils"
import { Checkbox } from "@/components/ui/checkbox"
import { StatusBadge } from "@/components/badges"
import { priorities, statuses } from "@/components/cases/categories"
import { AIGeneratedFlair } from "@/components/flair"
import { DataTableColumnHeader } from "@/components/table"

export const columns: ColumnDef<CaseRead>[] = [
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
    accessorKey: "id",
    header: ({ column }) => (
      <DataTableColumnHeader className="text-xs" column={column} title="ID" />
    ),
    cell: ({ row }) => {
      const id = row.getValue<CaseRead["id"]>("id").slice(0, 10)
      return <div className="w-[80px] text-xs">{id}...</div>
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
        (status) => status.value === row.getValue<CaseRead["status"]>("status")
      )

      if (!status) {
        return null
      }

      return (
        <div className="flex items-center space-x-2">
          {status.icon && (
            <status.icon className="size-3 text-muted-foreground" />
          )}
          <span className="text-xs">{status.label}</span>
        </div>
      )
    },
    filterFn: (row, id, value) => {
      return value.includes(row.getValue<CaseRead["id"]>(id))
    },
  },
  {
    accessorKey: "priority",
    header: ({ column }) => (
      <DataTableColumnHeader column={column} title="Priority" />
    ),
    cell: ({ row }) => {
      const priority = row.getValue<CaseRead["priority"]>("priority")
      const { label, icon: Icon } = priorities.find(
        (p) => p.value === priority
      )!

      return (
        <StatusBadge status={priority}>
          <Icon className="stroke-inherit/5 size-3" strokeWidth={3} />
          <span className="text-xs">{label}</span>
        </StatusBadge>
      )
    },
    filterFn: (row, id, value) => {
      return value.includes(row.getValue<CaseRead["id"]>(id))
    },
  },
  {
    accessorKey: "created_at",
    header: ({ column }) => (
      <DataTableColumnHeader column={column} title="Created At" />
    ),
    cell: ({ row }) => {
      const dt = new Date(row.getValue<CaseRead["created_at"]>("created_at"))
      const strDt = `${dt.toLocaleDateString()}, ${dt.toLocaleTimeString()}`
      return <span className="truncate text-xs">{strDt}</span>
    },
    filterFn: (row, id, value) => {
      return value.includes(row.getValue<CaseRead["id"]>(id))
    },
  },
  {
    accessorKey: "case_title",
    header: ({ column }) => (
      <DataTableColumnHeader column={column} title="Title" />
    ),
    cell: ({ row }) => {
      return (
        <div className="flex space-x-2">
          <span className="max-w-[300px] truncate text-xs">
            {row.getValue<CaseRead["case_title"]>("case_title")}
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
            {JSON.stringify(row.getValue<CaseRead["payload"]>("payload"))}
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
      const label = row.getValue<CaseRead["malice"]>("malice")
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
      const action = row.getValue<CaseRead["action"]>("action")
      return (
        <div className="flex space-x-2">
          <span
            className={cn(
              "max-w-[300px] truncate text-xs",
              !action && "text-muted-foreground"
            )}
          >
            {action}
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
      const caseContextFields = row.getValue<CaseRead["context"]>("context")
      return (
        <div className="flex space-x-2">
          <span className="max-w-[300px] space-x-1 truncate text-xs text-muted-foreground">
            {caseContextFields.length > 0
              ? caseContextFields.map(({ value }, idx) => (
                  <StatusBadge key={idx}>{value}</StatusBadge>
                ))
              : "No context tags"}
          </span>
        </div>
      )
    },
  },
  {
    accessorKey: "tags",
    header: ({ column }) => (
      <DataTableColumnHeader column={column} title="Tags" />
    ),
    cell: ({ row }) => {
      const tags = row.getValue<CaseRead["tags"]>("tags")
      return (
        <div className="flex space-x-2">
          <span className="max-w-[300px] flex-col space-y-1 text-xs text-muted-foreground">
            {tags.length > 0
              ? tags.map(({ tag, value }, idx) => (
                  <StatusBadge key={idx}>
                    <AIGeneratedFlair>
                      {tag}:{value}
                    </AIGeneratedFlair>
                  </StatusBadge>
                ))
              : "No tags"}
          </span>
        </div>
      )
    },
  },
]
