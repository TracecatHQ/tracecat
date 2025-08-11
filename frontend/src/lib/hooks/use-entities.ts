import { useQuery } from "@tanstack/react-query"
import {
  entitiesGetEntityType,
  entitiesListEntityTypes,
  entitiesListFields,
} from "@/client"

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
        includeInactive: false,
      })
      return response
    },
    enabled: !!entityId,
  })

  return { fields, fieldsIsLoading, fieldsError }
}
