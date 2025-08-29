"use client"

import { useMutation, useQueryClient } from "@tanstack/react-query"
import { useParams } from "next/navigation"
import { useEffect, useState } from "react"
import {
  type EntityFieldCreate,
  type EntityFieldRead,
  entitiesActivateField,
  entitiesCreateField,
  entitiesDeactivateField,
  entitiesDeleteField,
  entitiesUpdateField,
} from "@/client"
import { CreateFieldDialog } from "@/components/entities/create-field-dialog"
import { EditFieldDialog } from "@/components/entities/edit-field-dialog"
import { EntityFieldsTable } from "@/components/entities/entity-fields-table"
import { CenteredSpinner } from "@/components/loading/spinner"
import { AlertNotification } from "@/components/notifications"
import { toast } from "@/components/ui/use-toast"
import { useEntity, useEntityFields } from "@/hooks/use-entities"
import { entityEvents } from "@/lib/entity-events"
import { useLocalStorage } from "@/lib/hooks"
import { useWorkspaceId } from "@/providers/workspace-id"

export default function EntityDetailPage() {
  const params = useParams<{ entityId: string }>()

  if (!params) {
    return <AlertNotification level="error" message="Invalid entity ID." />
  }

  const { entityId } = params
  const workspaceId = useWorkspaceId()
  const queryClient = useQueryClient()
  const [includeInactive] = useLocalStorage("entities-include-inactive", false)
  const [createFieldOpen, setCreateFieldOpen] = useState(false)
  const [editFieldOpen, setEditFieldOpen] = useState(false)
  const [fieldToEdit, setFieldToEdit] = useState<EntityFieldRead | null>(null)

  // Listen for global header event to open add field dialog
  useEffect(() => entityEvents.onAddField(() => setCreateFieldOpen(true)), [])

  const { entity, entityIsLoading, entityError } = useEntity(
    workspaceId,
    entityId
  )
  const { fields, fieldsIsLoading, fieldsError } = useEntityFields(
    workspaceId,
    entityId,
    includeInactive
  )

  const { mutateAsync: createField, isPending: isCreatingField } = useMutation({
    mutationFn: async (data: EntityFieldCreate) =>
      await entitiesCreateField({
        workspaceId,
        entityId: entityId,
        requestBody: data,
      }),
    onSuccess: () => {
      queryClient.invalidateQueries({
        queryKey: ["entity-fields", workspaceId, entityId],
      })
      toast({ title: "Field created", description: "Field created." })
    },
    onError: (error) => {
      console.error("Failed to create field", error)
      toast({
        title: "Error creating field",
        description: "Failed to create field. Please try again.",
      })
    },
  })

  const { mutateAsync: deactivateField, isPending: deactivateFieldPending } =
    useMutation({
      mutationFn: async (fieldId: string) =>
        await entitiesDeactivateField({
          workspaceId,
          entityId: entityId,
          fieldId,
        }),
      onSuccess: () => {
        queryClient.invalidateQueries({
          queryKey: ["entity-fields", workspaceId, entityId],
        })
        toast({
          title: "Field archived",
          description: "Successfully archived field.",
        })
      },
      onError: (error) => {
        console.error("Failed to archive field", error)
        toast({
          title: "Error archiving field",
          description: "Failed to archive the field. Please try again.",
        })
      },
    })

  const { mutateAsync: activateField, isPending: activateFieldPending } =
    useMutation({
      mutationFn: async (fieldId: string) =>
        await entitiesActivateField({
          workspaceId,
          entityId: entityId,
          fieldId,
        }),
      onSuccess: () => {
        queryClient.invalidateQueries({
          queryKey: ["entity-fields", workspaceId, entityId],
        })
        toast({
          title: "Field restored",
          description: "Successfully restored field.",
        })
      },
      onError: (error) => {
        console.error("Failed to restore field", error)
        toast({
          title: "Error restoring field",
          description: "Failed to restore the field. Please try again.",
        })
      },
    })

  const { mutateAsync: deleteField, isPending: deleteFieldPending } =
    useMutation({
      mutationFn: async (fieldId: string) =>
        await entitiesDeleteField({
          workspaceId,
          entityId: entityId,
          fieldId,
        }),
      onSuccess: () => {
        queryClient.invalidateQueries({
          queryKey: ["entity-fields", workspaceId, entityId],
        })
        toast({
          title: "Field deleted",
          description: "Successfully deleted field.",
        })
      },
      onError: (error) => {
        console.error("Failed to delete field", error)
        toast({
          title: "Error deleting field",
          description: "Failed to delete the field. Please try again.",
        })
      },
    })

  if (entityIsLoading || fieldsIsLoading) return <CenteredSpinner />
  if (entityError)
    return <AlertNotification level="error" message={entityError.message} />
  if (!entity) return <AlertNotification level="error" message="Not found" />
  if (fieldsError)
    return <AlertNotification level="error" message={fieldsError.message} />

  return (
    <div className="size-full overflow-auto">
      <div className="container my-8 max-w-[1200px] space-y-4">
        <EntityFieldsTable
          fields={fields || []}
          onEditField={(field) => {
            setFieldToEdit(field)
            setEditFieldOpen(true)
          }}
          onDeleteField={deleteField}
          onDeactivateField={deactivateField}
          onReactivateField={activateField}
          isDeleting={
            deleteFieldPending || deactivateFieldPending || activateFieldPending
          }
        />
      </div>
      <CreateFieldDialog
        open={createFieldOpen}
        onOpenChange={setCreateFieldOpen}
        onSubmit={async (data) => {
          await createField(data)
        }}
        isSubmitting={isCreatingField}
      />
      <EditFieldDialog
        field={fieldToEdit}
        open={editFieldOpen}
        onOpenChange={(open) => {
          setEditFieldOpen(open)
          if (!open) setFieldToEdit(null)
        }}
        onSubmit={async (fieldId, data) => {
          try {
            await entitiesUpdateField({
              workspaceId,
              entityId: entityId,
              fieldId,
              requestBody: data,
            })
            queryClient.invalidateQueries({
              queryKey: ["entity-fields", workspaceId, entityId],
            })
            toast({
              title: "Field updated",
              description: "The field was updated successfully.",
            })
          } catch (error) {
            console.error("Failed to update field", error)
            toast({
              title: "Error updating field",
              description: "Failed to update the field. Please try again.",
            })
          }
        }}
      />
    </div>
  )
}
