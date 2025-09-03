"use client"

import { AlertTriangle, Loader2, RefreshCw } from "lucide-react"
import { useEffect, useMemo, useRef, useState } from "react"
import { type Control, type FieldValues, useForm } from "react-hook-form"
import type { EntityFieldRead } from "@/client"
import {
  YamlStyledEditor,
  type YamlStyledEditorRef,
} from "@/components/editor/codemirror/yaml-editor"
import { JsonViewWithControls } from "@/components/json-viewer"
import {
  Accordion,
  AccordionContent,
  AccordionItem,
  AccordionTrigger,
} from "@/components/ui/accordion"
import { Alert, AlertDescription, AlertTitle } from "@/components/ui/alert"
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
  Form,
  FormControl,
  FormField,
  FormItem,
  FormLabel,
} from "@/components/ui/form"
import { Label } from "@/components/ui/label"
import {
  Tooltip,
  TooltipContent,
  TooltipProvider,
  TooltipTrigger,
} from "@/components/ui/tooltip"
import { useEntity, useEntityFields } from "@/hooks/use-entities"
import { useCreateRecord } from "@/hooks/use-records"
import { getIconByName } from "@/lib/icons"
import { cn } from "@/lib/utils"
import { WorkflowProvider } from "@/providers/workflow"
import { useWorkspaceId } from "@/providers/workspace-id"

interface CreateRecordDialogProps {
  open?: boolean
  onOpenChange?: (open: boolean) => void
  workspaceId?: string
  entityId: string
  onSuccess?: () => void
}

interface FormData {
  entityId: string
  data: Record<string, unknown>
}

export function CreateRecordDialog({
  open: controlledOpen,
  onOpenChange: controlledOnOpenChange,
  workspaceId: propWorkspaceId,
  entityId,
  onSuccess,
}: CreateRecordDialogProps) {
  const workspaceId = propWorkspaceId ?? useWorkspaceId()
  const { entity } = useEntity(workspaceId, entityId)
  const { createRecord, createRecordIsPending } = useCreateRecord()

  const [internalOpen, setInternalOpen] = useState(false)

  const open = controlledOpen !== undefined ? controlledOpen : internalOpen
  const onOpenChange = controlledOnOpenChange || setInternalOpen

  const form = useForm<FormData>({
    defaultValues: {
      entityId: entityId,
      data: {},
    },
  })

  const yamlEditorRef = useRef<YamlStyledEditorRef | null>(null)
  const [submissionError, setSubmissionError] = useState<string | null>(null)

  const { fields, fieldsIsLoading, refetchFields } = useEntityFields(
    workspaceId,
    entityId,
    false
  )

  // Sample values for placeholders
  const placeholderForField = (f: EntityFieldRead): unknown => {
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
  }

  // Build example payload from field schema
  const examplePayload = useMemo(() => {
    if (!fields) return {}
    const ex: Record<string, unknown> = {}
    for (const f of fields as EntityFieldRead[]) {
      ex[f.key] = placeholderForField(f)
    }
    return ex
  }, [fields])

  // Prefill YAML editor with example payload when dialog opens
  useEffect(() => {
    if (!open || !entityId) return
    // Only set if current data is empty to avoid clobbering user input
    const current = form.getValues("data")
    const isEmpty =
      !current ||
      (Object.keys(current).length === 0 && current.constructor === Object)
    if (isEmpty) {
      form.setValue("data", examplePayload, { shouldDirty: false })
    }
  }, [open, entityId, examplePayload, form])

  const onSubmit = async (values: FormData) => {
    try {
      setSubmissionError(null)
      // Ensure latest buffer is committed from YAML editor to form
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
      await createRecord({
        workspaceId,
        entityId: values.entityId,
        data: recordData,
      })
      form.reset()
      setSubmissionError(null)
      onOpenChange(false)
      onSuccess?.()
    } catch (error) {
      const errorMessage =
        error instanceof Error
          ? error.message
          : "Failed to create record. Please check your data and try again."
      setSubmissionError(errorMessage)
    }
  }

  return (
    <Dialog open={open} onOpenChange={onOpenChange}>
      <DialogContent className="max-w-3xl">
        <DialogHeader>
          <DialogTitle>Create entity record</DialogTitle>
          <DialogDescription>
            Write the record payload as YAML.
          </DialogDescription>
        </DialogHeader>

        {submissionError && (
          <Alert variant="destructive">
            <AlertTriangle className="h-4 w-4" />
            <AlertTitle>Error creating record</AlertTitle>
            <AlertDescription>{submissionError}</AlertDescription>
          </Alert>
        )}

        <Form {...form}>
          <form onSubmit={form.handleSubmit(onSubmit)} className="space-y-4">
            {/* Display selected entity */}
            <div className="space-y-2">
              <Label className="text-sm font-medium">Entity</Label>
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

            {/* Schemas accordion with JSON viewer and controls */}
            <Accordion type="single" collapsible className="mb-3">
              <AccordionItem value="schemas">
                <div className="flex items-center justify-between pr-1">
                  <AccordionTrigger className="px-0 text-xs">
                    Schemas
                  </AccordionTrigger>
                  <TooltipProvider>
                    <Tooltip>
                      <TooltipTrigger asChild>
                        <button
                          type="button"
                          className={cn(
                            "text-muted-foreground hover:text-foreground",
                            fieldsIsLoading && "animate-spin"
                          )}
                          onClick={async (e) => {
                            e.stopPropagation()
                            if (!entityId) return
                            const result = await refetchFields()
                            if (result.data) {
                              // Rebuild example payload from fresh fields
                              const freshPayload: Record<string, unknown> = {}
                              for (const f of result.data as EntityFieldRead[]) {
                                freshPayload[f.key] = placeholderForField(f)
                              }
                              form.setValue("data", freshPayload, {
                                shouldDirty: true,
                              })
                            }
                          }}
                        >
                          <RefreshCw className="h-3.5 w-3.5" />
                        </button>
                      </TooltipTrigger>
                      <TooltipContent>Reset schema</TooltipContent>
                    </Tooltip>
                  </TooltipProvider>
                </div>
                <AccordionContent>
                  {entityId ? (
                    <div className="mb-3">
                      <JsonViewWithControls
                        src={examplePayload}
                        defaultExpanded
                        defaultTab="nested"
                        showControls={false}
                      />
                    </div>
                  ) : (
                    <div className="text-xs text-muted-foreground">
                      Select an entity to view its schema.
                    </div>
                  )}
                </AccordionContent>
              </AccordionItem>
            </Accordion>

            <FormField
              control={form.control}
              name="data"
              rules={{ required: "Please enter record data" }}
              render={() => (
                <FormItem>
                  <FormLabel>Record data</FormLabel>
                  <FormControl>
                    <div className="min-h-[200px]">
                      <WorkflowProvider workflowId="" workspaceId={workspaceId}>
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
            disabled={createRecordIsPending}
          >
            Cancel
          </Button>
          <Button
            onClick={form.handleSubmit(onSubmit)}
            disabled={createRecordIsPending}
          >
            {createRecordIsPending && (
              <Loader2 className="mr-2 h-4 w-4 animate-spin" />
            )}
            Create record
          </Button>
        </DialogFooter>
      </DialogContent>
    </Dialog>
  )
}
