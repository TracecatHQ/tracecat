import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query"
import {
  entitiesCreateRelation,
  entitiesDeleteRelation,
  entitiesGetEntity,
  entitiesListEntities,
  entitiesListFields,
  entitiesListRelations,
  entitiesUpdateField,
  entitiesUpdateRelation,
  type FieldMetadataUpdate,
  type RelationDefinitionCreate,
  type RelationDefinitionUpdate,
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
      const response = await entitiesListEntities({
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
      const response = await entitiesGetEntity({
        workspaceId,
        entityId,
      })
      return response
    },
    enabled: !!workspaceId && !!entityId,
  })

  return { entity, entityIsLoading, entityError }
}

export function useEntityFields(
  workspaceId: string,
  entityId: string,
  includeInactive = false
) {
  const {
    data: fields,
    isLoading: fieldsIsLoading,
    error: fieldsError,
  } = useQuery({
    queryKey: ["entity-fields", workspaceId, entityId, includeInactive],
    queryFn: async () => {
      const response = await entitiesListFields({
        workspaceId,
        entityId,
        includeInactive,
      })
      return response
    },
    enabled: !!workspaceId && !!entityId,
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

export function useEntityRelations(workspaceId: string, entityId: string) {
  const {
    data: relations,
    isLoading: relationsIsLoading,
    error: relationsError,
  } = useQuery({
    queryKey: ["entity-relations", workspaceId, entityId],
    queryFn: async () => {
      const response = await entitiesListRelations({
        workspaceId,
        entityId,
      })
      return response
    },
    enabled: !!workspaceId && !!entityId,
  })

  return { relations, relationsIsLoading, relationsError }
}

export function useCreateRelation(workspaceId: string, entityId: string) {
  const queryClient = useQueryClient()

  const { mutateAsync: createRelation, isPending: createRelationIsPending } =
    useMutation({
      mutationFn: async (data: RelationDefinitionCreate) => {
        return await entitiesCreateRelation({
          workspaceId,
          entityId,
          requestBody: data,
        })
      },
      onSuccess: () => {
        queryClient.invalidateQueries({
          queryKey: ["entity-relations", workspaceId, entityId],
        })
        toast({
          title: "Relation created",
          description: "The relation was created successfully.",
        })
      },
      onError: (error) => {
        console.error("Failed to create relation", error)
        toast({
          title: "Error creating relation",
          description: "Failed to create the relation. Please try again.",
          variant: "destructive",
        })
      },
    })

  return { createRelation, createRelationIsPending }
}

export function useUpdateRelation(workspaceId: string) {
  const queryClient = useQueryClient()

  const { mutateAsync: updateRelation, isPending: updateRelationIsPending } =
    useMutation({
      mutationFn: async ({
        relationId,
        data,
      }: {
        relationId: string
        data: RelationDefinitionUpdate
      }) => {
        return await entitiesUpdateRelation({
          workspaceId,
          relationId,
          requestBody: data,
        })
      },
      onSuccess: () => {
        queryClient.invalidateQueries({
          queryKey: ["entity-relations"],
        })
        toast({
          title: "Relation updated",
          description: "The relation was updated successfully.",
        })
      },
      onError: (error) => {
        console.error("Failed to update relation", error)
        toast({
          title: "Error updating relation",
          description: "Failed to update the relation. Please try again.",
          variant: "destructive",
        })
      },
    })

  return { updateRelation, updateRelationIsPending }
}

export function useDeleteRelation(workspaceId: string) {
  const queryClient = useQueryClient()

  const { mutateAsync: deleteRelation, isPending: deleteRelationIsPending } =
    useMutation({
      mutationFn: async (relationId: string) => {
        return await entitiesDeleteRelation({
          workspaceId,
          relationId,
        })
      },
      onSuccess: () => {
        queryClient.invalidateQueries({
          queryKey: ["entity-relations"],
        })
        toast({
          title: "Relation deleted",
          description: "The relation was deleted successfully.",
        })
      },
      onError: (error) => {
        console.error("Failed to delete relation", error)
        toast({
          title: "Error deleting relation",
          description: "Failed to delete the relation. Please try again.",
          variant: "destructive",
        })
      },
    })

  return { deleteRelation, deleteRelationIsPending }
}
