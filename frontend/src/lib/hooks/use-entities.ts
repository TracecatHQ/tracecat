import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query"
import {
  entitiesGetEntityType,
  entitiesListEntityTypes,
  entitiesListFields,
  entitiesUpdateField,
  type FieldMetadataUpdate,
} from "@/client"
import { toast } from "@/components/ui/use-toast"

export function useEntities(workspaceId: string, includeInactive = true) {
  const {
    data: entities,
    isLoading: entitiesIsLoading,
    error: entitiesError,
  } = useQuery({
    queryKey: ["entities", workspaceId, includeInactive],
    queryFn: async () => {
      const response = await entitiesListEntityTypes({
        workspaceId,
        includeInactive,
      })
      return response
    },
  })

  return { entities, entitiesIsLoading, entitiesError }
}

export function useEntity(workspaceId: string, entityId: string) {
  const {
    data: entity,
    isLoading: entityIsLoading,
    error: entityError,
  } = useQuery({
    queryKey: ["entity", workspaceId, entityId],
    queryFn: async () => {
      const response = await entitiesGetEntityType({
        workspaceId,
        entityId,
      })
      return response
    },
    enabled: !!entityId,
  })

  return { entity, entityIsLoading, entityError }
}

export function useEntityFields(workspaceId: string, entityId: string) {
  const {
    data: fields,
    isLoading: fieldsIsLoading,
    error: fieldsError,
  } = useQuery({
    queryKey: ["entity-fields", workspaceId, entityId],
    queryFn: async () => {
      const response = await entitiesListFields({
        workspaceId,
        entityId,
        includeInactive: true,
      })
      return response
    },
    enabled: !!entityId,
  })

  return { fields, fieldsIsLoading, fieldsError }
}

export function useUpdateEntityField(workspaceId: string, entityId: string) {
  const queryClient = useQueryClient()

  const { mutateAsync: updateField, isPending: updateFieldIsPending } =
    useMutation({
      mutationFn: async ({
        fieldId,
        data,
      }: {
        fieldId: string
        data: FieldMetadataUpdate
      }) => {
        return await entitiesUpdateField({
          workspaceId,
          fieldId,
          requestBody: data,
        })
      },
      onSuccess: () => {
        queryClient.invalidateQueries({
          queryKey: ["entity-fields", workspaceId, entityId],
        })
        toast({
          title: "Field updated",
          description: "The field was updated successfully.",
        })
      },
      onError: (error) => {
        console.error("Failed to update field", error)
        toast({
          title: "Error updating field",
          description: "Failed to update the field. Please try again.",
          variant: "destructive",
        })
      },
    })

  return { updateField, updateFieldIsPending }
}
