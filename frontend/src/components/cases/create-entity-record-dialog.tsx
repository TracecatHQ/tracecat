"use client"

import { CheckCheck, Copy, Loader2 } from "lucide-react"
import { useEffect, useMemo, useRef, useState } from "react"
import { useForm } from "react-hook-form"
import YAML from "yaml"
import type { CaseRecordLinkCreate, EntitySchemaField } from "@/client"
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
import { Button } from "@/components/ui/button"
// JSON example viewer (simple pretty-printed block)
import {
  Dialog,
  DialogContent,
  DialogDescription,
  DialogFooter,
  DialogHeader,
  DialogTitle,
} from "@/components/ui/dialog"
import { Form } from "@/components/ui/form"
import {
  Tooltip,
  TooltipContent,
  TooltipProvider,
  TooltipTrigger,
} from "@/components/ui/tooltip"
import { useCreateCaseRecord, useGetEntitySchema } from "@/lib/hooks"

interface CreateEntityRecordDialogProps {
  open: boolean
  onOpenChange: (open: boolean) => void
  entityId: string
  caseId: string
  workspaceId: string
  onSuccess?: () => void
}

export function CreateEntityRecordDialog({
  open,
  onOpenChange,
  entityId,
  caseId,
  workspaceId,
  onSuccess,
}: CreateEntityRecordDialogProps) {
  const { schema, isLoading: schemaLoading } = useGetEntitySchema({
    entityId: entityId,
    workspaceId,
  })

  const { createRecord, isCreating } = useCreateCaseRecord({
    caseId,
    workspaceId,
  })

  const form = useForm<{ record_data: Record<string, unknown> | undefined }>({
    defaultValues: { record_data: undefined },
  })
  const editorRef = useRef<YamlStyledEditorRef | null>(null)
  const [copied, setCopied] = useState(false)
  const [relationSchemas, setRelationSchemas] = useState<
    Map<string, EntitySchemaField[]>
  >(new Map())
  const [relationsFetched, setRelationsFetched] = useState(false)

  const isLoadingData = schemaLoading
  const hasFillableFields = useMemo(() => true, [])
  // Shared helpers to build placeholder values
  const placeholderForType = (t: string): unknown => {
    const type = t.toUpperCase()
    switch (type) {
      case "TEXT":
      case "STRING":
      case "LONGTEXT":
      case "TEXTAREA":
        return "text"
      case "INTEGER":
      case "INT":
        return 123
      case "NUMBER":
      case "FLOAT":
      case "DECIMAL":
        return 123.45
      case "BOOL":
      case "BOOLEAN":
        return true
      case "DATE":
        return "2025-01-01"
      case "DATETIME":
      case "TIMESTAMP":
        return "2025-01-01T12:00:00Z"
      case "JSON":
      case "OBJECT":
        return { key: "value" }
      default:
        return "value"
    }
  }

  const placeholderForField = (f: EntitySchemaField): unknown => {
    const t = f.type.toUpperCase()
    if (t === "SELECT" || t === "ENUM") {
      if (f.enum_options && f.enum_options.length > 0) return f.enum_options[0]
      return "option"
    }
    if (t === "MULTI_SELECT" || t === "MULTISELECT") {
      if (f.enum_options && f.enum_options.length > 0) {
        return f.enum_options.slice(
          0,
          Math.max(1, Math.min(2, f.enum_options.length))
        )
      }
      return ["item"]
    }
    if (t.startsWith("ARRAY_")) {
      const itemType = t.replace("ARRAY_", "")
      const sample = placeholderForType(itemType)
      return [sample, sample]
    }
    return placeholderForType(t)
  }
  // Build example JSON payload with placeholder values
  const examplePayload = useMemo(() => {
    if (!schema) return {}
    const ex: Record<string, unknown> = {}
    // Fields
    for (const f of schema.fields) {
      ex[f.key] = placeholderForField(f)
    }
    // Relations (use target entity field shapes when available)
    for (const r of schema.relations || []) {
      const relType = String(r.relation_type)
      const fields = relationSchemas.get(r.source_key)
      const buildObj = () => {
        const obj: Record<string, unknown> = {}
        if (fields && fields.length > 0) {
          for (const f of fields) {
            obj[f.key] = placeholderForField(f)
          }
        } else {
          obj["key"] = "value"
        }
        return obj
      }
      if (relType === "one_to_one" || relType === "many_to_one") {
        ex[r.source_key] = buildObj()
      } else {
        ex[r.source_key] = [buildObj()]
      }
    }
    return ex
  }, [schema, relationSchemas])

  // Fetch relation target schemas to show per-relation examples
  useEffect(() => {
    const fetchSchemas = async () => {
      if (!schema?.relations || schema.relations.length === 0) {
        setRelationSchemas(new Map())
        setRelationsFetched(true)
        return
      }
      setRelationsFetched(false)
      const m = new Map<string, EntitySchemaField[]>()
      try {
        const { entitiesGetEntitySchema } = await import("@/client")
        for (const r of schema.relations) {
          const target = await entitiesGetEntitySchema({
            entityId: r.target_entity_id,
            workspaceId,
          })
          const fields = (target.fields || []).filter(
            (f) => !f.type.startsWith("RELATION_")
          )
          m.set(r.source_key, fields)
        }
      } catch {}
      setRelationSchemas(m)
      setRelationsFetched(true)
    }
    fetchSchemas()
  }, [schema?.relations, workspaceId])

  const relationExamples = useMemo(() => {
    const map = new Map<string, Record<string, unknown>>()
    relationSchemas.forEach((fields, key) => {
      const obj: Record<string, unknown> = {}
      fields.forEach((f) => {
        obj[f.key] = placeholderForField(f)
      })
      map.set(key, obj)
    })
    return map
  }, [relationSchemas])

  const handleSubmit = async (values: {
    record_data?: Record<string, unknown>
  }) => {
    if (!entityId) return
    try {
      const recordData: CaseRecordLinkCreate = {
        entity_id: entityId,
        record_data: values.record_data || {},
      }
      await createRecord(recordData)
      onOpenChange(false)
      form.reset()
      onSuccess?.()
    } catch (error) {
      console.error("Failed to create entity record:", error)
    }
  }

  // Prefill YAML editor with full sample payload by default
  // Wait until relation schemas are ready (if any) to include full shapes
  const relationsCount = schema?.relations?.length || 0
  const relationSchemasReady = relationsCount === 0 || relationsFetched
  useEffect(() => {
    if (!open) return
    if (!schema) return
    if (!relationSchemasReady) return
    const current = form.getValues("record_data")
    const isEmpty =
      !current ||
      (typeof current === "object" && Object.keys(current).length === 0)
    if (isEmpty) {
      form.setValue("record_data", examplePayload, { shouldDirty: false })
    }
  }, [open, schema, examplePayload, relationSchemasReady])

  return (
    <Dialog open={open} onOpenChange={onOpenChange}>
      <DialogContent className="max-w-3xl max-h-[85vh] overflow-hidden flex flex-col">
        <DialogHeader>
          <DialogTitle>Create entity record</DialogTitle>
          <DialogDescription>
            Write the record payload as YAML. Include relation keys to create
            related records.
          </DialogDescription>
        </DialogHeader>

        <div className="flex-1 overflow-y-auto px-1 py-0.5">
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
                        className="mr-1 text-muted-foreground hover:text-foreground"
                        onClick={async (e) => {
                          e.stopPropagation()
                          try {
                            const yamlText = YAML.stringify(examplePayload, {
                              lineWidth: 0,
                              minContentWidth: 0,
                            })
                            await navigator.clipboard?.writeText(yamlText)
                          } catch {}
                          form.setValue("record_data", examplePayload, {
                            shouldDirty: true,
                          })
                          setCopied(true)
                          setTimeout(() => setCopied(false), 1200)
                        }}
                      >
                        {copied ? (
                          <CheckCheck className="h-3.5 w-3.5" />
                        ) : (
                          <Copy className="h-3.5 w-3.5" />
                        )}
                      </button>
                    </TooltipTrigger>
                    <TooltipContent>Copy sample payload</TooltipContent>
                  </Tooltip>
                </TooltipProvider>
              </div>
              <AccordionContent>
                <div className="mb-3">
                  <JsonViewWithControls
                    src={examplePayload}
                    defaultExpanded
                    defaultTab="nested"
                    showControls={false}
                  />
                </div>
                {relationExamples.size > 0 && (
                  <div className="mb-3 space-y-2">
                    {Array.from(relationExamples.entries()).map(
                      ([key, obj]) => (
                        <div key={key} className="space-y-1">
                          <div className="text-[11px] text-muted-foreground">
                            {key}
                          </div>
                          <JsonViewWithControls
                            src={obj}
                            defaultExpanded
                            defaultTab="nested"
                            showControls={false}
                          />
                        </div>
                      )
                    )}
                  </div>
                )}
              </AccordionContent>
            </AccordionItem>
          </Accordion>
          <Form {...form}>
            <form
              onSubmit={form.handleSubmit(handleSubmit)}
              className="space-y-4"
            >
              <YamlStyledEditor
                ref={editorRef}
                name="record_data"
                control={form.control}
              />
            </form>
          </Form>
        </div>

        <DialogFooter className="pt-4">
          <div className="flex items-center justify-end w-full">
            <div className="flex gap-2">
              <Button
                type="button"
                variant="outline"
                onClick={() => {
                  onOpenChange(false)
                  form.reset()
                }}
                disabled={isCreating}
              >
                Cancel
              </Button>
              <Button
                onClick={() => {
                  editorRef.current?.commitToForm()
                  form.handleSubmit(handleSubmit)()
                }}
                disabled={
                  !entityId ||
                  isCreating ||
                  isLoadingData ||
                  (schema && !hasFillableFields)
                }
              >
                {isCreating && (
                  <Loader2 className="mr-2 h-3 w-3 animate-spin" />
                )}
                Create
              </Button>
            </div>
          </div>
        </DialogFooter>
      </DialogContent>
    </Dialog>
  )
}
