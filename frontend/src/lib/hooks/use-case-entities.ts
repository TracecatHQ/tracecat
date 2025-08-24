import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query"
import type {
  CaseRecordLinkCreate,
  CaseRecordLinkRead,
  CaseRecordRead,
  EntityRead,
  EntitySchemaResponse,
  FieldMetadataRead,
  RecordUpdate,
} from "@/client"
import {
  casesAddRecordToCase,
  casesGetCaseRecord,
  casesListCaseRecords,
  casesRemoveRecordFromCase,
  casesUpdateCaseRecord,
  entitiesDeleteRecord,
  entitiesGetEntitySchema,
  entitiesListEntities,
  entitiesListFields,
} from "@/client"
import { toast } from "@/components/ui/use-toast"

export function useListCaseRecords({
  caseId,
  workspaceId,
  entityId,
}: {
  caseId: string
  workspaceId: string
  entityId?: string
}) {
  const {
    data: records,
    isLoading,
    error,
    refetch,
  } = useQuery<CaseRecordLinkRead[], Error>({
    queryKey: ["case-records", caseId, workspaceId, entityId],
    queryFn: async () => {
      const response = await casesListCaseRecords({
        caseId,
        workspaceId,
        entityId,
      })
      return response
    },
    enabled: !!caseId && !!workspaceId,
  })

  return { records, isLoading, error, refetch }
}

export function useCreateCaseRecord({
  caseId,
  workspaceId,
}: {
  caseId: string
  workspaceId: string
}) {
  const queryClient = useQueryClient()

  const {
    mutateAsync: createRecord,
    isPending: isCreating,
    error: createError,
  } = useMutation({
    mutationFn: async (data: CaseRecordLinkCreate) => {
      return await casesAddRecordToCase({
        caseId,
        workspaceId,
        requestBody: data,
      })
    },
    onSuccess: () => {
      queryClient.invalidateQueries({
        queryKey: ["case-records", caseId],
      })
      queryClient.invalidateQueries({
        queryKey: ["case-events", caseId],
      })
      toast({
        title: "Entity record added",
        description:
          "The entity record has been successfully linked to the case.",
      })
    },
    onError: (error) => {
      console.error("Failed to create case record:", error)
      toast({
        title: "Error",
        description: "Failed to add entity record. Please try again.",
        variant: "destructive",
      })
    },
  })

  return { createRecord, isCreating, createError }
}

export function useGetCaseRecord({
  caseId,
  recordId,
  workspaceId,
}: {
  caseId: string
  recordId: string
  workspaceId: string
}) {
  const {
    data: record,
    isLoading,
    error,
  } = useQuery<CaseRecordRead, Error>({
    queryKey: ["case-record", caseId, recordId, workspaceId],
    queryFn: async () => {
      const response = await casesGetCaseRecord({
        caseId,
        recordId,
        workspaceId,
      })
      return response
    },
    enabled: !!caseId && !!recordId && !!workspaceId,
  })

  return { record, isLoading, error }
}

export function useUpdateCaseRecord({
  caseId,
  recordId,
  workspaceId,
}: {
  caseId: string
  recordId: string
  workspaceId: string
}) {
  const queryClient = useQueryClient()

  const {
    mutateAsync: updateRecord,
    isPending: isUpdating,
    error: updateError,
  } = useMutation({
    mutationFn: async (data: RecordUpdate) => {
      return await casesUpdateCaseRecord({
        caseId,
        recordId,
        workspaceId,
        requestBody: data,
      })
    },
    onSuccess: () => {
      queryClient.invalidateQueries({
        queryKey: ["case-records", caseId],
      })
      queryClient.invalidateQueries({
        queryKey: ["case-record", caseId, recordId],
      })
      queryClient.invalidateQueries({
        queryKey: ["case-events", caseId],
      })
      toast({
        title: "Record updated",
        description: "The entity record has been successfully updated.",
      })
    },
    onError: (error) => {
      console.error("Failed to update case record:", error)
      toast({
        title: "Error",
        description: "Failed to update entity record. Please try again.",
        variant: "destructive",
      })
    },
  })

  return { updateRecord, isUpdating, updateError }
}

export function useDeleteCaseRecord({
  caseId,
  workspaceId,
}: {
  caseId: string
  workspaceId: string
}) {
  const queryClient = useQueryClient()

  const {
    mutateAsync: deleteRecord,
    isPending: isDeleting,
    error: deleteError,
  } = useMutation({
    mutationFn: async (recordId: string) => {
      return await entitiesDeleteRecord({
        recordId,
        workspaceId,
      })
    },
    onSuccess: () => {
      queryClient.invalidateQueries({
        queryKey: ["case-records", caseId],
      })
      queryClient.invalidateQueries({
        queryKey: ["case-events", caseId],
      })
      toast({
        title: "Record deleted",
        description: "The record was permanently deleted.",
      })
    },
    onError: (error) => {
      console.error("Failed to delete case record:", error)
      toast({
        title: "Error",
        description: "Failed to delete entity record. Please try again.",
        variant: "destructive",
      })
    },
  })

  return { deleteRecord, isDeleting, deleteError }
}

export function useRemoveCaseRecordLink({
  caseId,
  workspaceId,
}: {
  caseId: string
  workspaceId: string
}) {
  const queryClient = useQueryClient()

  const {
    mutateAsync: removeLink,
    isPending: isRemoving,
    error: removeError,
  } = useMutation({
    mutationFn: async (linkId: string) => {
      return await casesRemoveRecordFromCase({
        caseId,
        linkId,
        workspaceId,
      })
    },
    onSuccess: () => {
      queryClient.invalidateQueries({
        queryKey: ["case-records", caseId],
      })
      queryClient.invalidateQueries({
        queryKey: ["case-events", caseId],
      })
      toast({
        title: "Link removed",
        description: "The entity record has been unlinked from the case.",
      })
    },
    onError: (error) => {
      console.error("Failed to remove case record link:", error)
      toast({
        title: "Error",
        description: "Failed to unlink entity record. Please try again.",
        variant: "destructive",
      })
    },
  })

  return { removeLink, isRemoving, removeError }
}

export function useListEntities({
  workspaceId,
  includeInactive = false,
}: {
  workspaceId: string
  includeInactive?: boolean
}) {
  const {
    data: entities,
    isLoading,
    error,
  } = useQuery<EntityRead[], Error>({
    queryKey: ["entities", workspaceId, includeInactive],
    queryFn: async () => {
      const response = await entitiesListEntities({
        workspaceId,
        includeInactive,
      })
      return response
    },
    enabled: !!workspaceId,
  })

  return { entities, isLoading, error }
}

export function useGetEntitySchema({
  entityId,
  workspaceId,
}: {
  entityId: string
  workspaceId: string
}) {
  const {
    data: schema,
    isLoading,
    error,
  } = useQuery<EntitySchemaResponse, Error>({
    queryKey: ["entity-schema", entityId, workspaceId],
    queryFn: async () => {
      const response = await entitiesGetEntitySchema({
        entityId,
        workspaceId,
      })
      return response
    },
    enabled: !!entityId && !!workspaceId,
  })

  return { schema, isLoading, error }
}

export function useListEntityFields({
  entityId,
  workspaceId,
  includeInactive = false,
}: {
  entityId: string
  workspaceId: string
  includeInactive?: boolean
}) {
  const {
    data: fields,
    isLoading,
    error,
  } = useQuery<FieldMetadataRead[], Error>({
    queryKey: ["entity-fields", entityId, workspaceId, includeInactive],
    queryFn: async () => {
      const response = await entitiesListFields({
        entityId,
        workspaceId,
        includeInactive,
      })
      return response
    },
    enabled: !!entityId && !!workspaceId,
  })

  return { fields, isLoading, error }
}
