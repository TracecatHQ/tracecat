"use client"

import { zodResolver } from "@hookform/resolvers/zod"
import { Loader2 } from "lucide-react"
import { useEffect } from "react"
import { useForm } from "react-hook-form"
import { z } from "zod"
import type {
  CaseRecordLinkRead,
  EntitySchemaField,
  RecordUpdate,
} from "@/client"
import { EntityFieldInput } from "@/components/cases/entity-field-input"
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
import { Skeleton } from "@/components/ui/skeleton"
import { useGetEntitySchema, useUpdateCaseRecord } from "@/lib/hooks"

interface EditEntityRecordDialogProps {
  open: boolean
  onOpenChange: (open: boolean) => void
  caseId: string
  recordLink: CaseRecordLinkRead
  workspaceId: string
  onSuccess?: () => void
}

function generateZodSchema(
  fields: EntitySchemaField[]
): z.ZodObject<Record<string, z.ZodTypeAny>> {
  const schema: Record<string, z.ZodTypeAny> = {}

  fields.forEach((field) => {
    const fieldType = field.type.toUpperCase()
    let fieldSchema: z.ZodTypeAny

    switch (fieldType) {
      case "TEXT":
        fieldSchema = z.string()
        break
      case "INTEGER":
        fieldSchema = z.number().int()
        break
      case "NUMBER":
        fieldSchema = z.number()
        break
      case "BOOL":
        fieldSchema = z.boolean()
        break
      case "DATE":
        fieldSchema = z.string()
        break
      case "DATETIME":
        fieldSchema = z.string()
        break
      case "SELECT":
        if (field.enum_options && field.enum_options.length > 0) {
          fieldSchema = z.enum(field.enum_options as [string, ...string[]])
        } else {
          fieldSchema = z.string()
        }
        break
      case "MULTI_SELECT":
        fieldSchema = z.array(z.string())
        break
      case "ARRAY_TEXT":
        fieldSchema = z.array(z.string())
        break
      case "ARRAY_INTEGER":
        fieldSchema = z.array(z.number().int())
        break
      case "ARRAY_NUMBER":
        fieldSchema = z.array(z.number())
        break
      case "RELATION_BELONGS_TO":
        fieldSchema = z.string()
        break
      case "RELATION_HAS_MANY":
        fieldSchema = z.array(z.string())
        break
      default:
        fieldSchema = z.any()
    }

    // All fields are optional by default
    fieldSchema = fieldSchema.optional().nullable()

    schema[field.key] = fieldSchema
  })

  return z.object(schema)
}

export function EditEntityRecordDialog({
  open,
  onOpenChange,
  caseId,
  recordLink,
  workspaceId,
  onSuccess,
}: EditEntityRecordDialogProps) {
  const entityId = recordLink.record?.entity_id || recordLink.entity_id

  const { schema, isLoading: isLoadingSchema } = useGetEntitySchema({
    entityId,
    workspaceId,
  })

  const { updateRecord, isUpdating } = useUpdateCaseRecord({
    caseId,
    recordId: recordLink.record?.id || recordLink.record_id,
    workspaceId,
  })

  const form = useForm({
    resolver:
      schema?.fields && schema.fields.length > 0
        ? zodResolver(generateZodSchema(schema.fields))
        : undefined,
    defaultValues: {},
  })

  // Initialize form with existing data
  useEffect(() => {
    if (recordLink.record?.field_data && schema?.fields) {
      const formattedData: Record<string, unknown> = {}

      // Parse the field data and format it for the form
      Object.entries(recordLink.record.field_data).forEach(([key, value]) => {
        const field = schema.fields.find((f) => f.key === key)
        if (field) {
          // Handle special formatting for dates and arrays
          if (field.type === "DATE" || field.type === "DATETIME") {
            formattedData[key] = value as string
          } else if (
            field.type === "MULTI_SELECT" ||
            field.type === "ARRAY_TEXT" ||
            field.type === "ARRAY_INTEGER" ||
            field.type === "ARRAY_NUMBER" ||
            field.type === "RELATION_HAS_MANY"
          ) {
            formattedData[key] = Array.isArray(value) ? value : []
          } else {
            formattedData[key] = value
          }
        }
      })

      form.reset(formattedData)
    }
  }, [recordLink.record?.field_data, schema, form])

  const handleSubmit = async (values: Record<string, unknown>) => {
    try {
      const updateData: RecordUpdate = values
      await updateRecord(updateData)
      onOpenChange(false)
      onSuccess?.()
    } catch (error) {
      console.error("Failed to update entity record:", error)
    }
  }

  return (
    <>
      <Dialog open={open} onOpenChange={onOpenChange}>
        <DialogContent className="max-h-[80vh] max-w-2xl overflow-y-auto">
          <DialogHeader>
            <DialogTitle>Edit entity record</DialogTitle>
            <DialogDescription>
              Update the fields for this{" "}
              {schema?.entity?.display_name || "entity"} record
            </DialogDescription>
          </DialogHeader>

          {isLoadingSchema ? (
            <div className="space-y-4 py-4">
              <Skeleton className="h-10 w-full" />
              <Skeleton className="h-10 w-full" />
              <Skeleton className="h-10 w-full" />
            </div>
          ) : schema?.fields && schema.fields.length > 0 ? (
            <Form {...form}>
              <form
                onSubmit={form.handleSubmit(handleSubmit)}
                className="space-y-4"
              >
                {schema.fields.map((field) => (
                  <EntityFieldInput
                    key={field.key}
                    field={field}
                    control={form.control}
                    name={field.key}
                  />
                ))}

                <DialogFooter className="flex items-center justify-between">
                  <div className="flex gap-2">
                    <Button
                      type="button"
                      variant="outline"
                      onClick={() => onOpenChange(false)}
                      disabled={isUpdating}
                    >
                      Cancel
                    </Button>
                    <Button type="submit" disabled={isUpdating}>
                      {isUpdating ? (
                        <>
                          <Loader2 className="mr-2 h-4 w-4 animate-spin" />
                          Saving...
                        </>
                      ) : (
                        "Save changes"
                      )}
                    </Button>
                  </div>
                </DialogFooter>
              </form>
            </Form>
          ) : (
            <div className="py-8 text-center text-muted-foreground">
              No fields available for this entity
            </div>
          )}
        </DialogContent>
      </Dialog>
    </>
  )
}
