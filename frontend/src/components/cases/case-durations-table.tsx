"use client"

import { DotsHorizontalIcon } from "@radix-ui/react-icons"
import { Info } from "lucide-react"
import { useState } from "react"
import type { CaseDurationAnchorSelection, CaseDurationRead } from "@/client"
import {
  CASE_EVENT_FILTER_OPTIONS,
  getCaseEventOption,
  isCaseEventFilterType,
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
import {
  Tooltip,
  TooltipContent,
  TooltipTrigger,
} from "@/components/ui/tooltip"

interface CaseDurationsTableProps {
  durations: CaseDurationRead[]
  onDeleteDuration: (durationId: string) => Promise<void>
  isDeleting?: boolean
}

const SELECTION_LABELS: Record<CaseDurationAnchorSelection, string> = {
  first: "First seen",
  last: "Last seen",
}

const normalizeFilterValues = (value: unknown): string[] => {
  if (Array.isArray(value)) {
    return value.filter((item): item is string => typeof item === "string")
  }

  if (typeof value === "string") {
    return [value]
  }

  if (
    value &&
    typeof value === "object" &&
    Array.isArray((value as { $in?: unknown[] }).$in)
  ) {
    const inArray = (value as { $in: unknown[] }).$in
    return inArray.filter((item): item is string => typeof item === "string")
  }

  return []
}

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

    const selection = anchor.selection ?? "first"
    const valueLabels: string[] = []

    if (isCaseEventFilterType(anchor.event_type)) {
      const rawFilterValue = anchor.field_filters?.["data.new"]
      const values = normalizeFilterValues(rawFilterValue)

      for (const value of values) {
        const option = CASE_EVENT_FILTER_OPTIONS[
          anchor.event_type
        ].options.find((filterOption) => filterOption.value === value)

        valueLabels.push(option?.label ?? value)
      }
    }

    return (
      <div className="flex items-center gap-2 text-xs text-foreground">
        <Icon className="size-3.5 text-muted-foreground" aria-hidden />
        <span className="font-medium">{label}</span>
        {valueLabels.map((displayValue, index) => (
          <Badge
            key={`${anchor.event_type}-${displayValue}-${index}`}
            variant="secondary"
            className="px-2 py-0.5"
          >
            {displayValue}
          </Badge>
        ))}
        <Badge variant="outline" className="border-dashed px-2 py-0.5">
          {SELECTION_LABELS[selection]}
        </Badge>
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
              <div className="flex items-center gap-2 text-xs font-medium text-foreground">
                <span>{row.getValue<CaseDurationRead["name"]>("name")}</span>
                {row.original.description ? (
                  <Tooltip>
                    <TooltipTrigger asChild>
                      <button
                        type="button"
                        className="size-5 p-0 text-muted-foreground"
                        aria-label="View duration description"
                      >
                        <Info className="size-3" aria-hidden />
                      </button>
                    </TooltipTrigger>
                    <TooltipContent className="max-w-[240px] text-xs">
                      {row.original.description}
                    </TooltipContent>
                  </Tooltip>
                ) : null}
              </div>
            ),
            enableSorting: true,
            enableHiding: false,
          },
          {
            id: "start_anchor",
            header: () => (
              <span className="text-xs font-semibold">From event</span>
            ),
            cell: ({ row }) => renderAnchor(row.original.start_anchor),
            enableSorting: false,
            enableHiding: false,
          },
          {
            id: "end_anchor",
            header: () => (
              <span className="text-xs font-semibold">To event</span>
            ),
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
