"use client"

import { zodResolver } from "@hookform/resolvers/zod"
import { Database, Link, Loader2 } from "lucide-react"
import { useEffect, useMemo, useState } from "react"
import { useForm } from "react-hook-form"
import { z } from "zod"
import type { CaseRecordLinkCreate, EntitySchemaField } from "@/client"
import { EntityFieldInput } from "@/components/cases/entity-field-input"
import {
  Breadcrumb,
  BreadcrumbItem,
  BreadcrumbLink,
  BreadcrumbList,
  BreadcrumbPage,
  BreadcrumbSeparator,
} from "@/components/ui/breadcrumb"
import { Button } from "@/components/ui/button"
import {
  Carousel,
  CarouselContent,
  CarouselItem,
  CarouselNext,
  CarouselPrevious,
} from "@/components/ui/carousel"
import {
  Dialog,
  DialogContent,
  DialogDescription,
  DialogFooter,
  DialogHeader,
} from "@/components/ui/dialog"
import { Form } from "@/components/ui/form"
import { Skeleton } from "@/components/ui/skeleton"
import {
  useCreateCaseRecord,
  useGetEntitySchema,
  useListEntities,
  useListEntityFields,
} from "@/lib/hooks"

interface CreateEntityRecordDialogProps {
  open: boolean
  onOpenChange: (open: boolean) => void
  entityId: string
  caseId: string
  workspaceId: string
  onSuccess?: () => void
}

function generateZodSchema(
  fields: EntitySchemaField[],
  relationFields?: Map<string, EntitySchemaField[]>
): z.ZodObject<Record<string, z.ZodTypeAny>> {
  const schema: Record<string, z.ZodTypeAny> = {}

  // Process main entity fields
  fields.forEach((field) => {
    const fieldType = field.type.toUpperCase()
    let fieldSchema: z.ZodTypeAny

    switch (fieldType) {
      case "TEXT":
      case "STRING":
      case "LONGTEXT":
      case "TEXTAREA":
        fieldSchema = z.string().nullable().optional()
        break
      case "INTEGER":
      case "INT":
        fieldSchema = z.number().int().nullable().optional()
        break
      case "NUMBER":
      case "FLOAT":
      case "DECIMAL":
        fieldSchema = z.number().nullable().optional()
        break
      case "BOOL":
      case "BOOLEAN":
        fieldSchema = z.boolean().default(false)
        break
      case "DATE":
        fieldSchema = z.string().nullable().optional()
        break
      case "DATETIME":
      case "TIMESTAMP":
        fieldSchema = z.string().nullable().optional()
        break
      case "SELECT":
      case "ENUM":
        if (field.enum_options && field.enum_options.length > 0) {
          fieldSchema = z
            .enum(field.enum_options as [string, ...string[]])
            .nullable()
            .optional()
        } else {
          fieldSchema = z.string().nullable().optional()
        }
        break
      case "MULTI_SELECT":
      case "MULTISELECT":
        fieldSchema = z.array(z.string()).default([])
        break
      case "ARRAY_TEXT":
      case "ARRAY_STRING":
        fieldSchema = z.array(z.string()).default([])
        break
      case "ARRAY_INTEGER":
      case "ARRAY_INT":
        fieldSchema = z.array(z.number().int()).default([])
        break
      case "ARRAY_NUMBER":
      case "ARRAY_FLOAT":
        fieldSchema = z.array(z.number()).default([])
        break
      case "JSON":
      case "OBJECT":
        fieldSchema = z.any().optional()
        break
      case "RELATION_ONE_TO_ONE":
        // For one_to_one relations, we create a nested object for the related entity
        if (relationFields) {
          const relationKey = field.key
          const relatedFields = relationFields.get(relationKey)
          if (relatedFields) {
            // Create a nested schema for the related entity
            const nestedSchema: Record<string, z.ZodTypeAny> = {}
            relatedFields.forEach((relField) => {
              nestedSchema[relField.key] = generateFieldSchema(relField)
            })
            fieldSchema = z.object(nestedSchema).nullable().optional()
          } else {
            fieldSchema = z.any().nullable().optional()
          }
        } else {
          fieldSchema = z.any().nullable().optional()
        }
        break
      default:
        fieldSchema = z.any().optional()
    }

    schema[field.key] = fieldSchema
  })

  return z.object(schema)
}

// Helper function to generate schema for individual fields
function generateFieldSchema(field: EntitySchemaField): z.ZodTypeAny {
  const fieldType = field.type.toUpperCase()

  switch (fieldType) {
    case "TEXT":
    case "STRING":
    case "LONGTEXT":
    case "TEXTAREA":
      return z.string().nullable().optional()
    case "INTEGER":
    case "INT":
      return z.number().int().nullable().optional()
    case "NUMBER":
    case "FLOAT":
    case "DECIMAL":
      return z.number().nullable().optional()
    case "BOOL":
    case "BOOLEAN":
      return z.boolean().default(false)
    case "DATE":
      return z.string().nullable().optional()
    case "DATETIME":
    case "TIMESTAMP":
      return z.string().nullable().optional()
    case "SELECT":
    case "ENUM":
      if (field.enum_options && field.enum_options.length > 0) {
        return z
          .enum(field.enum_options as [string, ...string[]])
          .nullable()
          .optional()
      } else {
        return z.string().nullable().optional()
      }
    case "MULTI_SELECT":
    case "MULTISELECT":
      return z.array(z.string()).default([])
    case "ARRAY_TEXT":
    case "ARRAY_STRING":
      return z.array(z.string()).default([])
    case "ARRAY_INTEGER":
    case "ARRAY_INT":
      return z.array(z.number().int()).default([])
    case "ARRAY_NUMBER":
    case "ARRAY_FLOAT":
      return z.array(z.number()).default([])
    case "JSON":
    case "OBJECT":
      return z.any().optional()
    default:
      return z.any().optional()
  }
}

export function CreateEntityRecordDialog({
  open,
  onOpenChange,
  entityId,
  caseId,
  workspaceId,
  onSuccess,
}: CreateEntityRecordDialogProps) {
  const [currentStep, setCurrentStep] = useState<1 | 2>(1)
  const [relationSchemas, setRelationSchemas] = useState<
    Map<string, EntitySchemaField[]>
  >(new Map())
  const [relationEntityNames, setRelationEntityNames] = useState<
    Map<string, string>
  >(new Map())

  const { entities } = useListEntities({
    workspaceId,
    includeInactive: false,
  })

  const { schema, isLoading: schemaLoading } = useGetEntitySchema({
    entityId: entityId,
    workspaceId,
  })

  const { fields: fullFields, isLoading: fieldsLoading } = useListEntityFields({
    entityId: entityId,
    workspaceId,
  })

  const { createRecord, isCreating } = useCreateCaseRecord({
    caseId,
    workspaceId,
  })

  // Separate regular fields and relation fields
  const { regularFields, relationFields } = useMemo(() => {
    if (!schema?.fields) return { regularFields: [], relationFields: [] }

    const regular: EntitySchemaField[] = []
    const relations: Array<EntitySchemaField & { targetEntityId?: string }> = []

    schema.fields.forEach((field) => {
      const fieldType = field.type.toUpperCase()
      if (fieldType === "RELATION_ONE_TO_ONE") {
        // Find the full field metadata to get target entity ID
        const fullField = fullFields?.find((f) => f.field_key === field.key)
        relations.push({
          ...field,
          targetEntityId: fullField?.target_entity_id || undefined,
        })
      } else if (fieldType !== "RELATION_ONE_TO_MANY") {
        // Skip HAS_MANY relations as they're not created inline
        regular.push(field)
      }
    })

    return { regularFields: regular, relationFields: relations }
  }, [schema, fullFields])

  // Fetch schemas for relation fields
  useEffect(() => {
    const fetchRelationSchemas = async () => {
      if (relationFields.length === 0) {
        setRelationSchemas(new Map())
        setRelationEntityNames(new Map())
        return
      }

      const newSchemas = new Map<string, EntitySchemaField[]>()
      const newEntityNames = new Map<string, string>()

      for (const field of relationFields) {
        if (field.targetEntityId) {
          try {
            const { entitiesGetEntitySchema } = await import("@/client")
            const targetSchema = await entitiesGetEntitySchema({
              entityId: field.targetEntityId,
              workspaceId,
            })

            // Filter out relation fields to prevent recursive nesting
            const filteredFields = targetSchema.fields.filter((field) => {
              const fieldType = field.type.toUpperCase()
              return (
                fieldType !== "RELATION_ONE_TO_ONE" &&
                fieldType !== "RELATION_ONE_TO_MANY"
              )
            })

            newSchemas.set(field.key, filteredFields)
            newEntityNames.set(field.key, targetSchema.entity.display_name)
          } catch (error) {
            console.error(
              `Failed to fetch schema for relation field ${field.key}:`,
              error
            )
          }
        }
      }

      setRelationSchemas(newSchemas)
      setRelationEntityNames(newEntityNames)
    }

    fetchRelationSchemas()
  }, [relationFields, workspaceId])

  const formSchema = useMemo(() => {
    if (!schema) return z.object({})
    return generateZodSchema(schema.fields, relationSchemas)
  }, [schema, relationSchemas])

  const form = useForm({
    resolver: zodResolver(formSchema),
    defaultValues: {},
  })

  // Reset form when entity changes
  useEffect(() => {
    if (schema) {
      const defaultValues: Record<string, unknown> = {}

      // Set defaults for regular fields
      schema.fields.forEach((field) => {
        const fieldType = field.type.toUpperCase()
        if (fieldType === "BOOL" || fieldType === "BOOLEAN") {
          defaultValues[field.key] = false
        } else if (fieldType.includes("ARRAY") || fieldType.includes("MULTI")) {
          defaultValues[field.key] = []
        } else if (fieldType === "RELATION_ONE_TO_ONE") {
          // Initialize relation fields as null (not empty objects)
          defaultValues[field.key] = null
        } else {
          defaultValues[field.key] = null
        }
      })

      form.reset(defaultValues)
    }
  }, [schema, form])

  const handleSubmit = async (values: z.infer<typeof formSchema>) => {
    if (!entityId) return

    try {
      // Filter out empty relation objects and null values
      const filteredValues = Object.entries(values).reduce(
        (acc, [key, value]) => {
          // Skip null values and empty objects
          if (
            value !== null &&
            value !== undefined &&
            !(typeof value === "object" && Object.keys(value).length === 0)
          ) {
            acc[key] = value
          }
          return acc
        },
        {} as Record<string, unknown>
      )

      const recordData: CaseRecordLinkCreate = {
        entity_id: entityId,
        record_data: filteredValues,
      }

      await createRecord(recordData)
      onOpenChange(false)
      form.reset()
      onSuccess?.()
    } catch (error) {
      console.error("Failed to create entity record:", error)
    }
  }

  const isLoadingData =
    schemaLoading ||
    fieldsLoading ||
    (relationFields.length > 0 && relationSchemas.size < relationFields.length)

  // Check if there are any fillable fields (regular fields or relations with fields)
  const hasFillableFields = useMemo(() => {
    if (regularFields.length > 0) return true

    // Check if any relation has fillable fields
    for (const field of relationFields) {
      const relatedSchema = relationSchemas.get(field.key)
      if (relatedSchema && relatedSchema.length > 0) {
        return true
      }
    }

    return false
  }, [regularFields, relationFields, relationSchemas])

  // Helper function to handle step navigation
  const handleStepChange = (step: 1 | 2) => {
    setCurrentStep(step)
  }

  // Check if we have relations to show
  const hasRelations =
    relationFields.length > 0 &&
    relationFields.some((field) => {
      const relatedSchema = relationSchemas.get(field.key)
      return relatedSchema && relatedSchema.length > 0
    })

  // Set initial step when dialog opens
  useEffect(() => {
    if (open && schema) {
      if (regularFields.length > 0) {
        setCurrentStep(1)
      } else if (hasRelations) {
        setCurrentStep(2)
      }
    }
  }, [open, schema, regularFields.length, hasRelations])

  // Get selected entity name
  const selectedEntity = entities?.find((e) => e.id === entityId)

  return (
    <Dialog open={open} onOpenChange={onOpenChange}>
      <DialogContent className="max-w-3xl max-h-[85vh] overflow-hidden flex flex-col">
        <DialogHeader>
          <div className="space-y-4">
            {regularFields.length > 0 && hasRelations && (
              <Breadcrumb>
                <BreadcrumbList>
                  <BreadcrumbItem>
                    {currentStep === 1 ? (
                      <BreadcrumbPage className="flex items-center gap-2">
                        <Database className="h-4 w-4" />
                        Fields
                      </BreadcrumbPage>
                    ) : (
                      <BreadcrumbLink
                        className="flex items-center gap-2 cursor-pointer"
                        onClick={() => handleStepChange(1)}
                      >
                        <Database className="h-4 w-4" />
                        Fields
                      </BreadcrumbLink>
                    )}
                  </BreadcrumbItem>
                  <BreadcrumbSeparator />
                  <BreadcrumbItem>
                    {currentStep === 2 ? (
                      <BreadcrumbPage className="flex items-center gap-2">
                        <Link className="h-4 w-4" />
                        Related entities
                      </BreadcrumbPage>
                    ) : (
                      <BreadcrumbLink
                        className="flex items-center gap-2 cursor-pointer"
                        onClick={() => handleStepChange(2)}
                      >
                        <Link className="h-4 w-4" />
                        Related entities
                      </BreadcrumbLink>
                    )}
                  </BreadcrumbItem>
                </BreadcrumbList>
              </Breadcrumb>
            )}
            <DialogDescription>
              {currentStep === 1 &&
                regularFields.length > 0 &&
                `Fill in field data for ${selectedEntity?.display_name || "entity"}.`}
              {currentStep === 2 &&
                `Fill in field data for entities related to ${selectedEntity?.display_name || "entity"}.`}
              {!regularFields.length &&
                hasRelations &&
                `Fill in field data for entities related to ${selectedEntity?.display_name || "entity"}.`}
            </DialogDescription>
          </div>
        </DialogHeader>

        <div className="flex-1 overflow-y-auto px-1 py-0.5">
          <div className="space-y-6">
            {/* Step 1: Regular Fields */}
            {currentStep === 1 && regularFields.length > 0 && (
              <>
                {isLoadingData ? (
                  <div className="space-y-3">
                    <Skeleton className="h-8 w-full" />
                    <Skeleton className="h-8 w-full" />
                    <Skeleton className="h-8 w-full" />
                  </div>
                ) : schema ? (
                  <Form {...form}>
                    <form
                      onSubmit={form.handleSubmit(handleSubmit)}
                      className="space-y-6"
                    >
                      {/* Main Entity Fields - No Badge */}
                      <div className="space-y-4">
                        {regularFields.map((field) => (
                          <EntityFieldInput
                            key={field.key}
                            field={field}
                            control={form.control}
                            name={field.key}
                            disabled={isCreating}
                          />
                        ))}
                      </div>
                    </form>
                  </Form>
                ) : null}
              </>
            )}

            {/* Step 2: Relation Fields Carousel */}
            {currentStep === 2 && hasRelations && (
              <>
                {isLoadingData ? (
                  <div className="space-y-3">
                    <Skeleton className="h-8 w-full" />
                    <Skeleton className="h-8 w-full" />
                    <Skeleton className="h-8 w-full" />
                  </div>
                ) : schema ? (
                  <Form {...form}>
                    <form
                      onSubmit={form.handleSubmit(handleSubmit)}
                      className="space-y-6"
                    >
                      <div className="px-12">
                        <Carousel className="w-full">
                          <CarouselContent>
                            {(() => {
                              let visibleIndex = 0
                              const visibleRelationsCount =
                                relationFields.filter((f) => {
                                  const schema = relationSchemas.get(f.key)
                                  return schema && schema.length > 0
                                }).length

                              return relationFields.map((field) => {
                                const relatedSchema = relationSchemas.get(
                                  field.key
                                )
                                const entityName = relationEntityNames.get(
                                  field.key
                                )

                                if (
                                  !relatedSchema ||
                                  relatedSchema.length === 0
                                )
                                  return null

                                visibleIndex++
                                const currentIndex = visibleIndex

                                return (
                                  <CarouselItem key={field.key}>
                                    <div className="p-1">
                                      <div className="space-y-6 pt-3">
                                        <div>
                                          <h3 className="text-base font-medium">
                                            {field.display_name}
                                            {visibleRelationsCount > 1 && (
                                              <span className="text-muted-foreground ml-2 text-sm">
                                                ({currentIndex} of{" "}
                                                {visibleRelationsCount})
                                              </span>
                                            )}
                                          </h3>
                                          {entityName && (
                                            <p className="text-xs text-muted-foreground mt-1">
                                              Create new {entityName}
                                            </p>
                                          )}
                                        </div>
                                        <div className="space-y-4">
                                          {relatedSchema.map((relField) => (
                                            <EntityFieldInput
                                              key={`${field.key}.${relField.key}`}
                                              field={relField}
                                              control={form.control}
                                              name={`${field.key}.${relField.key}`}
                                              disabled={isCreating}
                                            />
                                          ))}
                                        </div>
                                      </div>
                                    </div>
                                  </CarouselItem>
                                )
                              })
                            })()}
                          </CarouselContent>
                          {relationFields.filter((field) => {
                            const relatedSchema = relationSchemas.get(field.key)
                            return relatedSchema && relatedSchema.length > 0
                          }).length > 1 && (
                            <>
                              <CarouselPrevious />
                              <CarouselNext />
                            </>
                          )}
                        </Carousel>
                      </div>
                    </form>
                  </Form>
                ) : null}
              </>
            )}

            {/* No fields message */}
            {!isLoadingData && schema && !hasFillableFields && (
              <div className="flex flex-col items-center justify-center py-8">
                <div className="p-2 rounded-full bg-muted/50 mb-3">
                  <Database className="h-5 w-5 text-muted-foreground" />
                </div>
                <h3 className="text-sm font-medium text-muted-foreground mb-1">
                  No fields found
                </h3>
                <p className="text-xs text-muted-foreground/75 text-center max-w-[250px]">
                  The selected entity requires at least one field to be filled
                  in.
                </p>
              </div>
            )}
          </div>
        </div>

        <DialogFooter className="pt-4">
          <div className="flex items-center justify-end w-full">
            <div className="flex gap-2">
              <Button
                type="button"
                variant="outline"
                onClick={() => {
                  onOpenChange(false)
                  setCurrentStep(1)
                  form.reset()
                }}
                disabled={isCreating}
              >
                Cancel
              </Button>

              {/* Next Button for Step 1 */}
              {currentStep === 1 &&
                regularFields.length > 0 &&
                hasRelations && (
                  <Button
                    onClick={() => setCurrentStep(2)}
                    disabled={isCreating}
                  >
                    Next
                  </Button>
                )}

              {/* Create Button for Final Step or Step 1 if no relations */}
              {((currentStep === 1 && !hasRelations) || currentStep === 2) && (
                <Button
                  onClick={form.handleSubmit(handleSubmit)}
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
              )}
            </div>
          </div>
        </DialogFooter>
      </DialogContent>
    </Dialog>
  )
}
