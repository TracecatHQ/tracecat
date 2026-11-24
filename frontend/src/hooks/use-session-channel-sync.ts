"use client"

import {
  type AgentSessionChannelSyncRead,
  agentSessionsGetSessionChannelSync,
  agentSessionsListSessionChannelDestinations,
  agentSessionsUpdateSessionChannelSync,
  type ChannelDestination,
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

/** Turn sync on or off; `destination` links a session that has no thread yet. */
export type ChannelSyncChange = {
  enabled: boolean
  destination?: ChannelDestination
}

/** Where a picked destination links the session, as a toast names it. */
export function channelDestinationLabel(destination: ChannelDestination) {
  return destination.kind === "direct_message"
    ? "your direct messages"
    : `#${destination.name}`
}

function syncChangedToast(change: ChannelSyncChange) {
  if (change.destination) {
    return {
      title: "Slack sync on",
      description: `Linked to ${channelDestinationLabel(change.destination)}. New messages here post to its thread.`,
    }
  }
  if (change.enabled) {
    return {
      title: "Slack sync on",
      description: "New messages here post to the Slack thread.",
    }
  }
  return {
    title: "Slack sync off",
    description: "Messages won't be synced to Slack.",
  }
}

/** Whether a session syncs with its channel thread, and a way to change it. */
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
  const { mutate: changeSync, isPending } = useMutation<
    AgentSessionChannelSyncRead,
    TracecatApiError,
    ChannelSyncChange
  >({
    mutationFn: async ({ enabled, destination }) =>
      await agentSessionsUpdateSessionChannelSync({
        workspaceId,
        sessionId: sessionId ?? "",
        requestBody: {
          enabled,
          destination: destination
            ? { kind: destination.kind, id: destination.id ?? null }
            : null,
        },
      }),
    onSuccess: (result, change) => {
      queryClient.setQueryData(queryKey, result)
      toast(syncChangedToast(change))
      if (change.destination) {
        // The Chats list marks linked sessions.
        void queryClient.invalidateQueries({ queryKey: ["chats", workspaceId] })
      }
    },
    onError: (error) => {
      toast({
        title: "Failed to change Slack sync",
        description: getApiErrorDetail(error) ?? "Please try again.",
        variant: "destructive",
      })
    },
  })
  return { sync, isLoading, changeSync, isPending }
}

/** Where the session's thread can open; read only while the picker is open. */
export function useSessionChannelDestinations(
  workspaceId: string,
  sessionId: string,
  enabled: boolean
) {
  const { data, isLoading, error } = useQuery<
    ChannelDestination[],
    TracecatApiError
  >({
    queryKey: ["agent-session-channel-destinations", workspaceId, sessionId],
    queryFn: async () =>
      (
        await agentSessionsListSessionChannelDestinations({
          workspaceId,
          sessionId,
        })
      ).items,
    enabled,
  })
  return { destinations: data, isLoading, error }
}
