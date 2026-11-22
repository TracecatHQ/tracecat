"use client"

import {
  type AgentSessionChannelSyncRead,
  agentSessionsGetSessionChannelSync,
  agentSessionsUpdateSessionChannelSync,
} from "@/client"
import { toast } from "@/components/ui/use-toast"
import { getApiErrorDetail, type TracecatApiError } from "@/lib/errors"
import { useMutation, useQuery, useQueryClient } from "@/lib/query"

/** Query key of a session's channel sync state. */
export function sessionChannelSyncQueryKey(
  workspaceId: string,
  sessionId: string
) {
  return ["agent-session-channel-sync", workspaceId, sessionId]
}

/** Whether a session syncs with its channel thread, and a setter for it. */
export function useSessionChannelSync(
  workspaceId: string,
  sessionId: string | undefined
) {
  const queryClient = useQueryClient()
  const queryKey = sessionChannelSyncQueryKey(workspaceId, sessionId ?? "")
  const { data: sync, isLoading } = useQuery<
    AgentSessionChannelSyncRead,
    TracecatApiError
  >({
    queryKey,
    queryFn: async () =>
      await agentSessionsGetSessionChannelSync({
        workspaceId,
        sessionId: sessionId ?? "",
      }),
    enabled: Boolean(sessionId),
  })
  const { mutate: setEnabled, isPending } = useMutation<
    AgentSessionChannelSyncRead,
    TracecatApiError,
    boolean
  >({
    mutationFn: async (enabled) =>
      await agentSessionsUpdateSessionChannelSync({
        workspaceId,
        sessionId: sessionId ?? "",
        requestBody: { enabled },
      }),
    onSuccess: (result) => {
      queryClient.setQueryData(queryKey, result)
    },
    onError: (error) => {
      toast({
        title: "Failed to change channel sync",
        description: getApiErrorDetail(error) ?? "Please try again.",
        variant: "destructive",
      })
    },
  })
  return { sync, isLoading, setEnabled, isPending }
}
