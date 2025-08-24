"use client"

import { CheckCheck, Copy, Loader2 } from "lucide-react"
import { useEffect, useMemo, useRef, useState } from "react"
import { useForm } from "react-hook-form"
import YAML from "yaml"
import type {
  EntityRead,
  EntitySchemaField,
  RelationDefinitionRead,
} from "@/client"
import { entitiesCreateRecord, entitiesGetEntitySchema } from "@/client"
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
import {
  Dialog,
  DialogContent,
  DialogDescription,
  DialogFooter,
  DialogHeader,
  DialogTitle,
} from "@/components/ui/dialog"
import { Form } from "@/components/ui/form"
import { Label } from "@/components/ui/label"
import {
  Select,
  SelectContent,
  SelectItem,
  SelectTrigger,
  SelectValue,
} from "@/components/ui/select"
import {
  Tooltip,
  TooltipContent,
  TooltipProvider,
  TooltipTrigger,
} from "@/components/ui/tooltip"
import { useEntities } from "@/lib/hooks/use-entities"

interface CreateRecordDialogProps {
  open: boolean
  onOpenChange: (open: boolean) => void
  workspaceId: string
  defaultEntityId?: string
  onSuccess?: (recordId: string) => void
}

export function CreateRecordDialog({
  open,
  onOpenChange,
  workspaceId,
  defaultEntityId,
  onSuccess,
}: CreateRecordDialogProps) {
  const { entities } = useEntities(workspaceId, false)
  const [entityId, setEntityId] = useState<string | undefined>(defaultEntityId)
  const [schema, setSchema] = useState<{
    fields: EntitySchemaField[]
    relations: RelationDefinitionRead[]
    entity: { id: string; name: string; display_name: string }
  } | null>(null)
  const [loadingSchema, setLoadingSchema] = useState(false)
  const editorRef = useRef<YamlStyledEditorRef | null>(null)
  const [copied, setCopied] = useState(false)
  const [relationSchemas, setRelationSchemas] = useState<
    Map<string, EntitySchemaField[]>
  >(new Map())
  const [relationsFetched, setRelationsFetched] = useState(false)
  const form = useForm<{ record_data?: Record<string, unknown> }>({
    defaultValues: { record_data: undefined },
  })

  // Fetch schema when entity changes
  useEffect(() => {
    const run = async () => {
      if (!entityId) {
        setSchema(null)
        return
      }
      try {
        setLoadingSchema(true)
        const s = await entitiesGetEntitySchema({ entityId, workspaceId })
        setSchema(s)
      } finally {
        setLoadingSchema(false)
      }
    }
    run().catch(() => setLoadingSchema(false))
  }, [entityId, workspaceId])

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

  const onSubmit = async (values: {
    record_data?: Record<string, unknown>
  }) => {
    if (!entityId) return
    const payload = values.record_data || {}
    const created = await entitiesCreateRecord({
      entityId,
      workspaceId,
      requestBody: payload,
    })
    onSuccess?.(created.id)
    onOpenChange(false)
    form.reset()
  }

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

        <div className="flex items-center gap-3 px-1">
          <Label
            htmlFor="record-entity-select"
            className="text-xs text-muted-foreground"
          >
            Entity
          </Label>
          <Select
            value={entityId || ""}
            onValueChange={(v) => setEntityId(v)}
            disabled={!entities || entities.length === 0}
          >
            <SelectTrigger id="record-entity-select" className="h-8 w-[220px]">
              <SelectValue placeholder="Select entity" />
            </SelectTrigger>
            <SelectContent>
              {(entities || []).map((e: EntityRead) => (
                <SelectItem key={e.id} value={e.id}>
                  {e.display_name}
                </SelectItem>
              ))}
            </SelectContent>
          </Select>
        </div>

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
                {schema ? (
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
            <form onSubmit={form.handleSubmit(onSubmit)} className="space-y-4">
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
                disabled={loadingSchema}
              >
                Cancel
              </Button>
              <Button
                onClick={() => {
                  editorRef.current?.commitToForm()
                  form.handleSubmit(onSubmit)()
                }}
                disabled={!entityId || loadingSchema}
              >
                {loadingSchema && (
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
