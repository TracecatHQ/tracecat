import { useMutation, useQueryClient } from "@tanstack/react-query"
import {
  entitiesCreateEntityRecord,
  entitiesDeleteEntityRecord,
  entitiesUpdateEntityRecord,
} from "@/client"

export function useCreateRecord() {
  const queryClient = useQueryClient()

  const { mutateAsync: createRecord, isPending: createRecordIsPending } =
    useMutation({
      mutationFn: async ({
        workspaceId,
        entityId,
        data,
      }: {
        workspaceId: string
        entityId: string
        data: Record<string, unknown>
      }) =>
        await entitiesCreateEntityRecord({
          workspaceId,
          entityId,
          requestBody: { data },
        }),
      onSuccess: (_, variables) => {
        queryClient.invalidateQueries({
          queryKey: ["records", variables.workspaceId],
        })
      },
    })

  return { createRecord, createRecordIsPending }
}

export function useUpdateRecord() {
  const queryClient = useQueryClient()

  const { mutateAsync: updateRecord, isPending: updateRecordIsPending } =
    useMutation({
      mutationFn: async ({
        workspaceId,
        entityId,
        recordId,
        data,
      }: {
        workspaceId: string
        entityId: string
        recordId: string
        data: Record<string, unknown>
      }) =>
        await entitiesUpdateEntityRecord({
          workspaceId,
          entityId,
          recordId,
          requestBody: { data },
        }),
      onSuccess: (_, variables) => {
        queryClient.invalidateQueries({
          queryKey: ["records", variables.workspaceId],
        })
        queryClient.invalidateQueries({
          queryKey: ["record", variables.workspaceId, variables.recordId],
        })
      },
    })

  return { updateRecord, updateRecordIsPending }
}

export function useDeleteRecord() {
  const queryClient = useQueryClient()

  const { mutateAsync: deleteRecord, isPending: deleteRecordIsPending } =
    useMutation({
      mutationFn: async ({
        workspaceId,
        entityId,
        recordId,
      }: {
        workspaceId: string
        entityId: string
        recordId: string
      }) =>
        await entitiesDeleteEntityRecord({
          workspaceId,
          entityId,
          recordId,
        }),
      onSuccess: (_, variables) => {
        queryClient.invalidateQueries({
          queryKey: ["records", variables.workspaceId],
        })
      },
    })

  return { deleteRecord, deleteRecordIsPending }
}
