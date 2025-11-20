"use client"

import { DotsHorizontalIcon } from "@radix-ui/react-icons"
import { Info } from "lucide-react"
import { useMemo, useState } from "react"
import type {
  CaseDurationAnchorSelection,
  CaseDurationDefinitionRead,
  CaseDurationDefinitionUpdate,
} from "@/client"
import {
  getFilterFieldKey,
  normalizeFilterValues,
} from "@/components/cases/case-duration-dialog"
import {
  CASE_EVENT_FILTER_OPTIONS,
  getCaseEventOption,
  isCaseEventFilterType,
  isCaseTagEventType,
} from "@/components/cases/case-duration-options"
import { UpdateCaseDurationDialog } from "@/components/cases/update-case-duration-dialog"
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
  DropdownMenuSeparator,
  DropdownMenuTrigger,
} from "@/components/ui/dropdown-menu"
import {
  Tooltip,
  TooltipContent,
  TooltipTrigger,
} from "@/components/ui/tooltip"
import { useCaseTagCatalog } from "@/lib/hooks"
import { useWorkspaceId } from "@/providers/workspace-id"

interface CaseDurationsTableProps {
  durations: CaseDurationDefinitionRead[]
  onDeleteDuration: (durationId: string) => Promise<void>
  isDeleting?: boolean
  onUpdateDuration: (
    durationId: string,
    payload: CaseDurationDefinitionUpdate
  ) => Promise<void>
  isUpdating?: boolean
  updatingDurationId?: string | null
}

const SELECTION_LABELS: Record<CaseDurationAnchorSelection, string> = {
  first: "First seen",
  last: "Last seen",
}

const defaultToolbarProps: DataTableToolbarProps<CaseDurationDefinitionRead> = {
  filterProps: {
    placeholder: "Filter durations...",
    column: "name",
  },
}

export function CaseDurationsTable({
  durations,
  onDeleteDuration,
  isDeleting,
  onUpdateDuration,
  isUpdating,
  updatingDurationId,
}: CaseDurationsTableProps) {
  const [selectedDuration, setSelectedDuration] =
    useState<CaseDurationDefinitionRead | null>(null)
  const [editingDuration, setEditingDuration] =
    useState<CaseDurationDefinitionRead | null>(null)
  const [isUpdateDialogOpen, setIsUpdateDialogOpen] = useState(false)

  const workspaceId = useWorkspaceId()
  const { caseTags } = useCaseTagCatalog(workspaceId ?? "", {
    enabled: Boolean(workspaceId),
  })

  const tagLabelByRef = useMemo(() => {
    const map = new Map<string, string>()
    if (!caseTags) {
      return map
    }

    for (const tag of caseTags) {
      map.set(tag.ref, tag.name)
    }

    return map
  }, [caseTags])

  const handleUpdateDialogChange = (open: boolean) => {
    setIsUpdateDialogOpen(open)
    if (!open) {
      setEditingDuration(null)
    }
  }

  const isDialogUpdating = useMemo(() => {
    if (!isUpdating) {
      return false
    }

    if (!editingDuration) {
      return isUpdating
    }

    if (!updatingDurationId) {
      return isUpdating
    }

    return editingDuration.id === updatingDurationId
  }, [isUpdating, editingDuration, updatingDurationId])

  const renderAnchor = (anchor: CaseDurationDefinitionRead["start_anchor"]) => {
    const { icon: Icon, label } = getCaseEventOption(anchor.event_type)

    const selection = anchor.selection ?? "first"
    const valueLabels: string[] = []

    const filterFieldKey = getFilterFieldKey(anchor.event_type)
    if (filterFieldKey) {
      const rawFilterValue = anchor.field_filters?.[filterFieldKey]
      const values = normalizeFilterValues(rawFilterValue)

      for (const value of values) {
        if (isCaseEventFilterType(anchor.event_type)) {
          const option = CASE_EVENT_FILTER_OPTIONS[
            anchor.event_type
          ].options.find((filterOption) => filterOption.value === value)

          valueLabels.push(option?.label ?? value)
        } else if (isCaseTagEventType(anchor.event_type)) {
          valueLabels.push(tagLabelByRef.get(value) ?? value)
        } else {
          valueLabels.push(value)
        }
      }
    }

    return (
      <div className="flex flex-col gap-2 text-xs text-foreground">
        <div className="flex items-center gap-2 whitespace-nowrap">
          <Icon
            className="h-4 w-4 flex-none text-muted-foreground"
            aria-hidden
          />
          <span className="font-medium">{label}</span>
        </div>
        <div className="flex flex-wrap items-center gap-2">
          {valueLabels.map((displayValue, index) => (
            <Badge
              key={`${anchor.event_type}-${displayValue}-${index}`}
              variant="secondary"
              className="px-2 py-0.5 whitespace-nowrap"
            >
              {displayValue}
            </Badge>
          ))}
          <Badge
            variant="outline"
            className="border-dashed px-2 py-0.5 whitespace-nowrap"
          >
            {SELECTION_LABELS[selection]}
          </Badge>
        </div>
      </div>
    )
  }

  return (
    <>
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
                  <span>
                    {row.getValue<CaseDurationDefinitionRead["name"]>("name")}
                  </span>
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
              meta: {
                headerStyle: { width: "18%" },
                cellStyle: { width: "18%" },
              },
            },
            {
              id: "start_anchor",
              header: () => (
                <span className="text-xs font-semibold">From event</span>
              ),
              cell: ({ row }) => renderAnchor(row.original.start_anchor),
              enableSorting: false,
              enableHiding: false,
              meta: {
                headerStyle: { width: "41%" },
                cellStyle: { width: "41%", verticalAlign: "top" },
              },
            },
            {
              id: "end_anchor",
              header: () => (
                <span className="text-xs font-semibold">To event</span>
              ),
              cell: ({ row }) => renderAnchor(row.original.end_anchor),
              enableSorting: false,
              enableHiding: false,
              meta: {
                headerStyle: { width: "41%" },
                cellStyle: { width: "41%", verticalAlign: "top" },
              },
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
                      <DropdownMenuItem
                        onClick={() => {
                          setEditingDuration(row.original)
                          setIsUpdateDialogOpen(true)
                        }}
                      >
                        Update duration
                      </DropdownMenuItem>
                      <DropdownMenuSeparator />
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
      <UpdateCaseDurationDialog
        open={isUpdateDialogOpen}
        onOpenChange={handleUpdateDialogChange}
        duration={editingDuration}
        onUpdateDuration={onUpdateDuration}
        isUpdating={isDialogUpdating}
      />
    </>
  )
}
