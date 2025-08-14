"use client"

import { useMutation, useQueryClient } from "@tanstack/react-query"
import { Settings2Icon } from "lucide-react"
import { useParams } from "next/navigation"
import { useEffect, useState } from "react"
import {
  entitiesCreateField,
  entitiesCreateRelationField,
  entitiesDeactivateField,
  entitiesDeleteField,
  entitiesReactivateField,
  type FieldMetadataRead,
  type FieldType,
  type RelationSettings,
} from "@/client"
import { CreateFieldDialog } from "@/components/entities/create-field-dialog"
import { EditFieldDialog } from "@/components/entities/edit-field-dialog"
import { EntityFieldsTable } from "@/components/entities/entity-fields-table"
import { CenteredSpinner } from "@/components/loading/spinner"
import { AlertNotification } from "@/components/notifications"
import { Card, CardContent } from "@/components/ui/card"
import { toast } from "@/components/ui/use-toast"
import { entityEvents } from "@/lib/entity-events"
import {
  useEntities,
  useEntity,
  useEntityFields,
  useUpdateEntityField,
} from "@/lib/hooks/use-entities"
import { useWorkspace } from "@/providers/workspace"

export default function EntityDetailPage() {
  const { workspaceId } = useWorkspace()
  const params = useParams<{ entityId: string }>()
  const entityId = params.entityId
  const queryClient = useQueryClient()
  const [createFieldDialogOpen, setCreateFieldDialogOpen] = useState(false)
  const [createFieldError, setCreateFieldError] = useState<string | null>(null)
  const [editFieldDialogOpen, setEditFieldDialogOpen] = useState(false)
  const [selectedFieldForEdit, setSelectedFieldForEdit] =
    useState<FieldMetadataRead | null>(null)

  const { entity, entityIsLoading, entityError } = useEntity(
    workspaceId,
    entityId
  )
  const { fields, fieldsIsLoading, fieldsError } = useEntityFields(
    workspaceId,
    entityId
  )
  const { updateField, updateFieldIsPending } = useUpdateEntityField(
    workspaceId,
    entityId
  )
  const { entities } = useEntities(workspaceId)

  // Set up the callback for the Add Field button in header
  useEffect(() => {
    const handleAddField = () => setCreateFieldDialogOpen(true)
    const unsubscribe = entityEvents.onAddField(handleAddField)
    return unsubscribe
  }, [])

  const { mutateAsync: createFieldMutation } = useMutation({
    mutationFn: async (data: {
      field_key: string
      field_type: string
      display_name: string
      description?: string
      enum_options?: string[]
      relation_settings?: RelationSettings
      default_value?: unknown
    }) => {
      // Use different endpoint for relation fields
      if (
        data.field_type === "RELATION_BELONGS_TO" ||
        data.field_type === "RELATION_HAS_MANY"
      ) {
        return await entitiesCreateRelationField({
          workspaceId,
          entityId,
          requestBody: {
            field_key: data.field_key,
            field_type: data.field_type as FieldType,
            display_name: data.display_name,
            description: data.description,
            relation_settings: data.relation_settings,
          },
        })
      } else {
        return await entitiesCreateField({
          workspaceId,
          entityId,
          requestBody: {
            field_key: data.field_key,
            field_type: data.field_type as FieldType,
            display_name: data.display_name,
            description: data.description,
            enum_options: data.enum_options,
            default_value: data.default_value,
          },
        })
      }
    },
    onSuccess: () => {
      queryClient.invalidateQueries({
        queryKey: ["entity-fields", workspaceId, entityId],
      })
      queryClient.invalidateQueries({
        queryKey: ["entity-field-counts", workspaceId],
      })
      toast({
        title: "Field created",
        description: "The field was created successfully.",
      })
    },
    onError: (error: unknown) => {
      console.error("Failed to create field", error)
      // Try to extract a user-friendly message if available
      let message = "Failed to create the field. Please try again."
      if (error && typeof error === "object") {
        const err = error as { body?: { detail?: string }; message?: string }
        message = err.body?.detail || err.message || message
      }
      setCreateFieldError(message)
    },
  })

  const { mutateAsync: deactivateFieldMutation, isPending: isDeactivating } =
    useMutation({
      mutationFn: async (fieldId: string) => {
        return await entitiesDeactivateField({
          workspaceId,
          fieldId,
        })
      },
      onSuccess: () => {
        queryClient.invalidateQueries({
          queryKey: ["entity-fields", workspaceId, entityId],
        })
        toast({
          title: "Field deactivated",
          description: "The field was deactivated successfully.",
        })
      },
      onError: (error) => {
        console.error("Failed to deactivate field", error)
        toast({
          title: "Error deactivating field",
          description: "Failed to deactivate the field. Please try again.",
          variant: "destructive",
        })
      },
    })

  const { mutateAsync: reactivateFieldMutation } = useMutation({
    mutationFn: async (fieldId: string) => {
      return await entitiesReactivateField({
        workspaceId,
        fieldId,
      })
    },
    onSuccess: () => {
      queryClient.invalidateQueries({
        queryKey: ["entity-fields", workspaceId, entityId],
      })
      toast({
        title: "Field reactivated",
        description: "The field was reactivated successfully.",
      })
    },
    onError: (error) => {
      console.error("Failed to reactivate field", error)
      toast({
        title: "Error reactivating field",
        description: "Failed to reactivate the field. Please try again.",
        variant: "destructive",
      })
    },
  })

  const { mutateAsync: deleteFieldMutation, isPending: isDeletingField } =
    useMutation({
      mutationFn: async (fieldId: string) => {
        return await entitiesDeleteField({
          workspaceId,
          fieldId,
        })
      },
      onSuccess: () => {
        queryClient.invalidateQueries({
          queryKey: ["entity-fields", workspaceId, entityId],
        })
        queryClient.invalidateQueries({
          queryKey: ["entity-field-counts", workspaceId],
        })
        toast({
          title: "Field deleted",
          description: "The field and all its data were permanently deleted.",
        })
      },
      onError: (error) => {
        console.error("Failed to delete field", error)
        toast({
          title: "Error deleting field",
          description: "Failed to delete the field. Please try again.",
          variant: "destructive",
        })
      },
    })

  if (entityIsLoading || fieldsIsLoading) {
    return <CenteredSpinner />
  }

  if (entityError || !entity) {
    return (
      <AlertNotification
        level="error"
        message={`Error loading entity: ${entityError?.message || "Unknown error"}`}
      />
    )
  }

  if (fieldsError || !fields) {
    return (
      <AlertNotification
        level="error"
        message={`Error loading fields: ${fieldsError?.message || "Unknown error"}`}
      />
    )
  }

  return (
    <div className="size-full overflow-auto">
      <div className="container max-w-[1200px] my-16">
        <div className="space-y-4">
          {fields.length === 0 ? (
            <Card>
              <CardContent className="flex flex-col items-center justify-center py-12">
                <Settings2Icon className="h-12 w-12 text-muted-foreground mb-4" />
                <h3 className="text-sm font-semibold mb-1">No fields yet</h3>
                <p className="text-xs text-muted-foreground text-center max-w-[300px]">
                  Add fields to define the structure of your{" "}
                  {entity.display_name.toLowerCase()} records
                </p>
              </CardContent>
            </Card>
          ) : (
            <EntityFieldsTable
              fields={fields}
              entities={entities}
              currentEntityName={entity?.display_name}
              onEditField={(field) => {
                setSelectedFieldForEdit(field)
                setEditFieldDialogOpen(true)
              }}
              onDeleteField={async (fieldId) => {
                await deleteFieldMutation(fieldId)
              }}
              onDeactivateField={async (fieldId) => {
                await deactivateFieldMutation(fieldId)
              }}
              onReactivateField={async (fieldId) => {
                await reactivateFieldMutation(fieldId)
              }}
              isDeleting={isDeactivating || isDeletingField}
            />
          )}
        </div>
      </div>

      <CreateFieldDialog
        open={createFieldDialogOpen}
        onOpenChange={(open) => {
          setCreateFieldDialogOpen(open)
          if (!open) {
            setCreateFieldError(null) // reset error when closing dialog
          }
        }}
        errorMessage={createFieldError || undefined}
        onSubmit={async (data) => {
          try {
            setCreateFieldError(null) // clear previous errors
            await createFieldMutation(data)
          } catch (error) {
            console.error("Failed to create field:", error)
          }
        }}
      />

      <EditFieldDialog
        field={selectedFieldForEdit}
        open={editFieldDialogOpen}
        onOpenChange={(open) => {
          setEditFieldDialogOpen(open)
          if (!open) {
            setSelectedFieldForEdit(null)
          }
        }}
        onSubmit={async (fieldId, data) => {
          try {
            await updateField({ fieldId, data })
          } catch (error) {
            // Error is already handled by mutation's onError callback
            console.error("Failed to update field:", error)
          }
        }}
        isPending={updateFieldIsPending}
      />
    </div>
  )
}
