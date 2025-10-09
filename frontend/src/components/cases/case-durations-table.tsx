"use client"

import { DotsHorizontalIcon } from "@radix-ui/react-icons"
import { useState } from "react"

import type { CaseDurationRead } from "@/types/case-durations"
import {
  CASE_DURATION_SELECTION_OPTIONS,
  getCaseEventOption,
} from "@/components/cases/case-duration-options"
import {
  DataTable,
  DataTableColumnHeader,
  type DataTableToolbarProps,
} from "@/components/data-table"
import {
  AlertDialog,
  AlertDialogAction,
  AlertDialogCancel,
  AlertDialogContent,
  AlertDialogDescription,
  AlertDialogFooter,
  AlertDialogHeader,
  AlertDialogTitle,
  AlertDialogTrigger,
} from "@/components/ui/alert-dialog"
import { Badge } from "@/components/ui/badge"
import { Button } from "@/components/ui/button"
import {
  DropdownMenu,
  DropdownMenuContent,
  DropdownMenuItem,
  DropdownMenuTrigger,
} from "@/components/ui/dropdown-menu"

interface CaseDurationsTableProps {
  durations: CaseDurationRead[]
  onDeleteDuration: (durationId: string) => Promise<void>
  isDeleting?: boolean
}

const SELECTION_LABELS = Object.fromEntries(
  CASE_DURATION_SELECTION_OPTIONS.map((option) => [option.value, option.label])
) as Record<CaseDurationRead["start_anchor"]["selection"], string>

const defaultToolbarProps: DataTableToolbarProps<CaseDurationRead> = {
  filterProps: {
    placeholder: "Filter durations...",
    column: "name",
  },
}

export function CaseDurationsTable({
  durations,
  onDeleteDuration,
  isDeleting,
}: CaseDurationsTableProps) {
  const [selectedDuration, setSelectedDuration] =
    useState<CaseDurationRead | null>(null)

  const renderAnchor = (anchor: CaseDurationRead["start_anchor"]) => {
    const { icon: Icon, label } = getCaseEventOption(anchor.event_type)
    const filters = Object.entries(anchor.field_filters ?? {})

    return (
      <div className="flex flex-col gap-2 text-xs">
        <div className="flex items-center gap-2 text-foreground">
          <span className="flex size-6 items-center justify-center rounded-full bg-muted">
            <Icon className="size-3.5 text-muted-foreground" aria-hidden />
          </span>
          <span className="font-medium">{label}</span>
        </div>
        <div className="flex flex-wrap items-center gap-2 text-[11px] text-muted-foreground">
          <Badge variant="outline" className="border-dashed px-2 py-0.5">
            {SELECTION_LABELS[anchor.selection] || anchor.selection}
          </Badge>
          <Badge variant="secondary" className="px-2 py-0.5">
            Timestamp: {anchor.timestamp_path}
          </Badge>
        </div>
        {filters.length > 0 ? (
          <div className="flex flex-wrap gap-2">
            {filters.map(([key, value]) => (
              <Badge key={key} variant="secondary" className="px-2 py-0.5">
                <span className="font-medium">{key}</span>
                <span className="text-muted-foreground">: {String(value)}</span>
              </Badge>
            ))}
          </div>
        ) : (
          <p className="text-[11px] text-muted-foreground">No filters</p>
        )}
      </div>
    )
  }

  return (
    <AlertDialog
      onOpenChange={(isOpen) => {
        if (!isOpen) {
          setSelectedDuration(null)
        }
      }}
    >
      <DataTable
        data={durations}
        columns={[
          {
            accessorKey: "name",
            header: ({ column }) => (
              <DataTableColumnHeader column={column} title="Name" />
            ),
            cell: ({ row }) => (
              <div className="text-xs font-medium text-foreground">
                {row.getValue<CaseDurationRead["name"]>("name")}
              </div>
            ),
            enableSorting: true,
            enableHiding: false,
          },
          {
            accessorKey: "description",
            header: ({ column }) => (
              <DataTableColumnHeader column={column} title="Description" />
            ),
            cell: ({ row }) => (
              <div className="text-xs text-muted-foreground">
                {row.getValue<CaseDurationRead["description"]>("description") ||
                  "-"}
              </div>
            ),
            enableSorting: false,
            enableHiding: true,
          },
          {
            id: "start_anchor",
            header: () => <span className="text-xs font-semibold">From event</span>,
            cell: ({ row }) => renderAnchor(row.original.start_anchor),
            enableSorting: false,
            enableHiding: false,
          },
          {
            id: "end_anchor",
            header: () => <span className="text-xs font-semibold">To event</span>,
            cell: ({ row }) => renderAnchor(row.original.end_anchor),
            enableSorting: false,
            enableHiding: false,
          },
          {
            id: "actions",
            enableHiding: false,
            cell: ({ row }) => (
              <div className="flex justify-end">
                <DropdownMenu>
                  <DropdownMenuTrigger asChild>
                    <Button variant="ghost" className="size-8 p-0">
                      <span className="sr-only">Open menu</span>
                      <DotsHorizontalIcon className="size-4" />
                    </Button>
                  </DropdownMenuTrigger>
                  <DropdownMenuContent align="end">
                    <AlertDialogTrigger asChild>
                      <DropdownMenuItem
                        className="text-rose-500 focus:text-rose-600"
                        onClick={() => setSelectedDuration(row.original)}
                      >
                        Delete duration
                      </DropdownMenuItem>
                    </AlertDialogTrigger>
                  </DropdownMenuContent>
                </DropdownMenu>
              </div>
            ),
          },
        ]}
        toolbarProps={defaultToolbarProps}
      />
      <AlertDialogContent>
        <AlertDialogHeader>
          <AlertDialogTitle>Delete duration</AlertDialogTitle>
          <AlertDialogDescription>
            Are you sure you want to delete
            {selectedDuration ? (
              <span className="font-semibold">
                {` ${selectedDuration.name}`}
              </span>
            ) : (
              " this duration"
            )}
            ? This action cannot be undone.
          </AlertDialogDescription>
        </AlertDialogHeader>
        <AlertDialogFooter>
          <AlertDialogCancel disabled={isDeleting}>Cancel</AlertDialogCancel>
          <AlertDialogAction
            variant="destructive"
            disabled={isDeleting}
            onClick={async () => {
              if (!selectedDuration) {
                return
              }

              await onDeleteDuration(selectedDuration.id)
              setSelectedDuration(null)
            }}
          >
            Delete
          </AlertDialogAction>
        </AlertDialogFooter>
      </AlertDialogContent>
    </AlertDialog>
  )
}
