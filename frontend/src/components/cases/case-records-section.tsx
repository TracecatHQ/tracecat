"use client"

import { DotsHorizontalIcon } from "@radix-ui/react-icons"
import type { ColumnDef } from "@tanstack/react-table"
import { formatDistanceToNow } from "date-fns"
import { BoxIcon, Loader2, Pencil, Trash2, Unlink } from "lucide-react"
import { useCallback, useEffect, useMemo, useRef, useState } from "react"
import type { Control, FieldValues } from "react-hook-form"
import { useForm } from "react-hook-form"
import type { CaseRecordRead, EntityFieldRead, EntityRead } from "@/client"
import { DataTable, DataTableColumnHeader } from "@/components/data-table"
import {
  YamlStyledEditor,
  type YamlStyledEditorRef,
} from "@/components/editor/codemirror/yaml-editor"
import { EntitySelectorPopover } from "@/components/entities/entity-selector-popover"
import { CompactJsonViewer } from "@/components/json-viewer-compact"
import { Avatar, AvatarFallback } from "@/components/ui/avatar"
import { Badge } from "@/components/ui/badge"
import { Button } from "@/components/ui/button"
import {
  Dialog,
  DialogContent,
  DialogDescription,
  DialogFooter,
  DialogHeader,
  DialogTitle,
} from "@/components/ui/dialog"
import {
  DropdownMenu,
  DropdownMenuContent,
  DropdownMenuItem,
  DropdownMenuTrigger,
} from "@/components/ui/dropdown-menu"
import {
  Form,
  FormControl,
  FormField,
  FormItem,
  FormLabel,
} from "@/components/ui/form"
import { Skeleton } from "@/components/ui/skeleton"
import { useEntities, useEntity, useEntityFields } from "@/hooks/use-entities"
import { useCaseRecords, useCreateCaseRecord } from "@/lib/hooks"
import { getIconByName } from "@/lib/icons"
import { capitalizeFirst } from "@/lib/utils"
import { WorkflowProvider } from "@/providers/workflow"

interface CaseRecordsSectionProps {
  caseId: string
  workspaceId: string
}

export function CaseRecordsSection({
  caseId,
  workspaceId,
}: CaseRecordsSectionProps) {
  const { records, recordsIsLoading, recordsError } = useCaseRecords({
    caseId,
    workspaceId,
  })
  const { entities } = useEntities(workspaceId)
  const [_selectedRecord, setSelectedRecord] = useState<CaseRecordRead | null>(
    null
  )
  const [_editDialogOpen, setEditDialogOpen] = useState(false)
  const [_deleteDialogOpen, setDeleteDialogOpen] = useState(false)
  const [createDialogOpen, setCreateDialogOpen] = useState(false)
  const [selectedEntityId, setSelectedEntityId] = useState<string>("")
  const [selectedEntityKey, setSelectedEntityKey] = useState<string>("")

  const entityById = useMemo(() => {
    const map = new Map<string, EntityRead>()
    entities?.forEach((entity) => map.set(entity.id, entity))
    return map
  }, [entities])

  const handleEditRecord = (record: CaseRecordRead) => {
    setSelectedRecord(record)
    setEditDialogOpen(true)
  }

  const handleUnlinkRecord = (record: CaseRecordRead) => {
    // TODO: Implement unlink functionality
    console.log("Unlink record:", record)
  }

  const handleDeleteRecord = (record: CaseRecordRead) => {
    setSelectedRecord(record)
    setDeleteDialogOpen(true)
  }

  const handleEntitySelect = (entity: EntityRead) => {
    setSelectedEntityId(entity.id)
    setSelectedEntityKey(entity.key)
    setCreateDialogOpen(true)
  }

  const columns: ColumnDef<CaseRecordRead>[] = [
    {
      accessorKey: "entity_id",
      header: ({ column }) => (
        <DataTableColumnHeader
          className="text-xs"
          column={column}
          title="Entity"
        />
      ),
      cell: ({ row }) => {
        const entity = entityById.get(row.original.entity_id)
        const IconComponent = entity?.icon
          ? getIconByName(entity.icon)
          : undefined
        const initials = entity?.display_name?.[0]?.toUpperCase() || "?"

        return (
          <div className="flex items-center gap-2.5">
            <Avatar className="size-7 shrink-0">
              <AvatarFallback className="text-xs">
                {IconComponent ? (
                  <IconComponent className="size-4" />
                ) : (
                  initials
                )}
              </AvatarFallback>
            </Avatar>
            <div>
              <div className="text-sm font-medium">
                {row.original.entity_display_name ||
                  entity?.display_name ||
                  row.original.entity_id}
              </div>
              <div className="text-xs text-muted-foreground">
                {row.original.entity_key ||
                  entity?.key ||
                  row.original.entity_id}
              </div>
            </div>
          </div>
        )
      },
      enableSorting: false,
      enableHiding: false,
    },
    {
      accessorKey: "created_at",
      header: ({ column }) => (
        <DataTableColumnHeader
          className="text-xs"
          column={column}
          title="Created"
        />
      ),
      cell: ({ row }) => (
        <div className="text-xs text-muted-foreground">
          {capitalizeFirst(
            formatDistanceToNow(new Date(row.original.created_at), {
              addSuffix: true,
            })
          )}
        </div>
      ),
      enableSorting: true,
      enableHiding: false,
    },
    {
      accessorKey: "updated_at",
      header: ({ column }) => (
        <DataTableColumnHeader
          className="text-xs"
          column={column}
          title="Updated"
        />
      ),
      cell: ({ row }) => (
        <div className="text-xs text-muted-foreground">
          {capitalizeFirst(
            formatDistanceToNow(new Date(row.original.updated_at), {
              addSuffix: true,
            })
          )}
        </div>
      ),
      enableSorting: true,
      enableHiding: false,
    },
    {
      accessorKey: "data",
      header: ({ column }) => (
        <DataTableColumnHeader
          className="text-xs"
          column={column}
          title="Record"
        />
      ),
      cell: ({ row }) => {
        const data = row.original.data || {}
        return (
          <div className="max-w-xs">
            <CompactJsonViewer src={data} />
          </div>
        )
      },
      enableSorting: false,
      enableHiding: false,
    },
    {
      id: "actions",
      enableHiding: false,
      cell: ({ row }) => {
        return (
          <div className="flex justify-end">
            <DropdownMenu>
              <DropdownMenuTrigger asChild>
                <Button
                  variant="ghost"
                  className="size-8 p-0"
                  onClick={(e) => e.stopPropagation()}
                >
                  <span className="sr-only">Open menu</span>
                  <DotsHorizontalIcon className="size-4" />
                </Button>
              </DropdownMenuTrigger>
              <DropdownMenuContent align="end">
                <DropdownMenuItem
                  onClick={() => handleEditRecord(row.original)}
                >
                  <Pencil className="mr-2 h-3 w-3" />
                  <span>Edit</span>
                </DropdownMenuItem>
                <DropdownMenuItem
                  onClick={() => handleUnlinkRecord(row.original)}
                >
                  <Unlink className="mr-2 h-3 w-3" />
                  <span>Unlink from case</span>
                </DropdownMenuItem>
                <DropdownMenuItem
                  onClick={() => handleDeleteRecord(row.original)}
                >
                  <Trash2 className="mr-2 h-3 w-3 text-rose-600" />
                  <span className="text-rose-600">Delete</span>
                </DropdownMenuItem>
              </DropdownMenuContent>
            </DropdownMenu>
          </div>
        )
      },
    },
  ]

  if (recordsIsLoading) {
    return (
      <div className="space-y-4">
        <div className="flex items-center justify-between">
          <Skeleton className="h-8 w-32" />
          <Skeleton className="h-8 w-24" />
        </div>
        <Skeleton className="h-[200px] w-full" />
      </div>
    )
  }

  if (recordsError) {
    return (
      <div className="flex flex-col items-center justify-center py-8">
        <p className="text-sm text-muted-foreground">
          Failed to load case records
        </p>
        <p className="text-xs text-muted-foreground mt-1">
          {recordsError.message}
        </p>
      </div>
    )
  }

  return (
    <div className="space-y-4">
      <div className="flex items-center justify-between">
        <div className="text-sm text-muted-foreground ml-3">
          {records?.length || 0} record{records?.length !== 1 ? "s" : ""} linked
          to this case
        </div>
        <EntitySelectorPopover
          entities={entities}
          onSelect={handleEntitySelect}
          buttonText="Add record"
        />
      </div>

      {records && records.length > 0 ? (
        <DataTable<CaseRecordRead, unknown>
          data={records}
          columns={columns}
          isLoading={recordsIsLoading}
          error={recordsError as Error | null}
          emptyMessage="No records linked to this case"
          tableId={`${workspaceId}-case-${caseId}-records`}
        />
      ) : (
        <NoRecords />
      )}

      {selectedEntityId && (
        <CreateCaseRecordDialog
          open={createDialogOpen}
          onOpenChange={(open) => {
            setCreateDialogOpen(open)
            if (!open) {
              setSelectedEntityId("")
              setSelectedEntityKey("")
            }
          }}
          caseId={caseId}
          workspaceId={workspaceId}
          entityId={selectedEntityId}
          entityKey={selectedEntityKey}
          onSuccess={() => {
            setSelectedEntityId("")
            setSelectedEntityKey("")
          }}
        />
      )}
    </div>
  )
}

function NoRecords() {
  return (
    <div className="flex flex-col items-center justify-center py-12">
      <div className="p-3 rounded-full bg-muted/50 mb-3">
        <BoxIcon className="h-6 w-6 text-muted-foreground" />
      </div>
      <h3 className="text-sm font-medium text-muted-foreground mb-1">
        No records linked
      </h3>
      <p className="text-xs text-muted-foreground/75 text-center max-w-[250px]">
        Add records to track entities related to this case
      </p>
    </div>
  )
}

// Dialog component for creating a record directly linked to the case
function CreateCaseRecordDialog({
  open,
  onOpenChange,
  caseId,
  workspaceId,
  entityId,
  entityKey,
  onSuccess,
}: {
  open: boolean
  onOpenChange: (open: boolean) => void
  caseId: string
  workspaceId: string
  entityId: string
  entityKey: string
  onSuccess?: () => void
}) {
  const { createCaseRecord, createCaseRecordIsPending } = useCreateCaseRecord({
    caseId,
    workspaceId,
  })
  const { entity } = useEntity(workspaceId, entityId)
  const { fields } = useEntityFields(workspaceId, entityId, false)
  const yamlEditorRef = useRef<YamlStyledEditorRef | null>(null)
  const [submissionError, setSubmissionError] = useState<string | null>(null)

  const form = useForm<{ data: Record<string, unknown> }>({
    defaultValues: {
      data: {},
    },
  })

  const placeholderForField = useCallback((f: EntityFieldRead): unknown => {
    const t = String(f.type).toUpperCase()
    if (t === "SELECT") {
      const options = (f.options || []).map((o) => o.key)
      return options[0] ?? "option"
    }
    if (t === "MULTI_SELECT") {
      const options = (f.options || []).map((o) => o.key)
      return options.length > 0
        ? options.slice(0, Math.max(1, Math.min(2, options.length)))
        : ["item"]
    }
    switch (t) {
      case "TEXT":
        return "text"
      case "INTEGER":
        return 123
      case "NUMBER":
        return 123.45
      case "BOOL":
        return true
      case "DATE":
        return "2025-01-01"
      case "DATETIME":
        return "2025-01-01T12:00:00Z"
      case "JSON":
        return { key: "value" }
      default:
        return "value"
    }
  }, [])

  // Build example payload from field schema
  const examplePayload = useMemo(() => {
    if (!fields) return {}
    const ex: Record<string, unknown> = {}
    for (const f of fields as EntityFieldRead[]) {
      ex[f.key] = placeholderForField(f)
    }
    return ex
  }, [fields, placeholderForField])

  // Set initial value when dialog opens
  useEffect(() => {
    if (open && examplePayload) {
      form.setValue("data", examplePayload)
    }
  }, [open, examplePayload, form])

  const onSubmit = async (values: { data: Record<string, unknown> }) => {
    try {
      setSubmissionError(null)
      yamlEditorRef.current?.commitToForm()

      const recordData = values.data
      if (
        recordData === null ||
        typeof recordData !== "object" ||
        Array.isArray(recordData)
      ) {
        setSubmissionError(
          "Invalid data format. Please provide a valid YAML object."
        )
        return
      }

      await createCaseRecord({
        entity_key: entityKey,
        data: recordData,
      })

      form.reset()
      setSubmissionError(null)
      onOpenChange(false)
      onSuccess?.()
    } catch (error) {
      const errorMessage =
        error instanceof Error && error.message
          ? error.message
          : "Failed to create record. Please check your data and try again."
      setSubmissionError(errorMessage)
    }
  }

  return (
    <>
      <Dialog open={open} onOpenChange={onOpenChange}>
        <DialogContent className="max-w-3xl">
          <DialogHeader>
            <DialogTitle>Create record for case</DialogTitle>
            <DialogDescription>
              Create a new {entity?.display_name || "entity"} record that will
              be linked to this case.
            </DialogDescription>
          </DialogHeader>

          {submissionError && (
            <div className="rounded-md bg-destructive/10 p-3 text-sm text-destructive">
              {submissionError}
            </div>
          )}

          <Form {...form}>
            <form onSubmit={form.handleSubmit(onSubmit)} className="space-y-4">
              {/* Display selected entity */}
              <div className="space-y-2">
                <label className="text-sm font-medium">Entity</label>
                <div className="flex items-center gap-2 text-sm text-muted-foreground">
                  {entity?.icon &&
                    (() => {
                      const IconComponent = getIconByName(entity.icon)
                      return IconComponent ? (
                        <IconComponent className="h-4 w-4" />
                      ) : null
                    })()}
                  <span className="font-medium text-foreground">
                    {entity?.display_name || "Loading..."}
                  </span>
                  {entity?.key && (
                    <Badge variant="secondary" className="text-xs">
                      {entity.key}
                    </Badge>
                  )}
                </div>
              </div>

              <FormField
                control={form.control}
                name="data"
                rules={{ required: "Please enter record data" }}
                render={() => (
                  <FormItem>
                    <FormLabel>Record data</FormLabel>
                    <FormControl>
                      <div className="min-h-[200px]">
                        <WorkflowProvider
                          workflowId=""
                          workspaceId={workspaceId}
                        >
                          <YamlStyledEditor
                            ref={yamlEditorRef}
                            name={"data"}
                            control={
                              form.control as unknown as Control<FieldValues>
                            }
                          />
                        </WorkflowProvider>
                      </div>
                    </FormControl>
                  </FormItem>
                )}
              />
            </form>
          </Form>

          <DialogFooter>
            <Button
              type="button"
              variant="outline"
              onClick={() => {
                setSubmissionError(null)
                onOpenChange(false)
              }}
              disabled={createCaseRecordIsPending}
            >
              Cancel
            </Button>
            <Button
              onClick={form.handleSubmit(onSubmit)}
              disabled={createCaseRecordIsPending}
            >
              {createCaseRecordIsPending && (
                <Loader2 className="mr-2 h-4 w-4 animate-spin" />
              )}
              Create record
            </Button>
          </DialogFooter>
        </DialogContent>
      </Dialog>
    </>
  )
}
