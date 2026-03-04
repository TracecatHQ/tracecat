"use client"

import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query"
import {
  type WatchtowerAgentRead,
  watchtowerDisableWatchtowerAgent,
  watchtowerEnableWatchtowerAgent,
  watchtowerListWatchtowerAgentSessions,
  watchtowerListWatchtowerAgents,
  watchtowerListWatchtowerSessionToolCalls,
  watchtowerRevokeWatchtowerSession,
} from "@/client"

const WATCHTOWER_REFRESH_MS = 5000

export function useWatchtowerAgents(params?: {
  status?: string
  agentType?: string
  limit?: number
}) {
  const status = params?.status
  const agentType = params?.agentType
  const limit = params?.limit ?? 100

  return useQuery({
    queryKey: ["watchtower", "agents", { status, agentType, limit }],
    queryFn: () =>
      watchtowerListWatchtowerAgents({
        status,
        agentType,
        limit,
      }),
    refetchInterval: WATCHTOWER_REFRESH_MS,
    staleTime: 2000,
  })
}

export function useWatchtowerAgentSessions(
  agentId: string | null,
  params?: {
    workspaceId?: string
    state?: string
    limit?: number
  }
) {
  const workspaceId = params?.workspaceId
  const state = params?.state
  const limit = params?.limit ?? 100

  return useQuery({
    queryKey: [
      "watchtower",
      "sessions",
      agentId,
      { workspaceId, state, limit },
    ],
    queryFn: () => {
      if (!agentId) {
        throw new Error("Missing agent ID")
      }
      return watchtowerListWatchtowerAgentSessions({
        agentId,
        workspaceId,
        state,
        limit,
      })
    },
    enabled: Boolean(agentId),
    refetchInterval: WATCHTOWER_REFRESH_MS,
    staleTime: 2000,
  })
}

export function useWatchtowerSessionToolCalls(
  sessionId: string | null,
  params?: {
    status?: string
    limit?: number
  }
) {
  const status = params?.status
  const limit = params?.limit ?? 100

  return useQuery({
    queryKey: ["watchtower", "tool-calls", sessionId, { status, limit }],
    queryFn: () => {
      if (!sessionId) {
        throw new Error("Missing session ID")
      }
      return watchtowerListWatchtowerSessionToolCalls({
        sessionId,
        status,
        limit,
      })
    },
    enabled: Boolean(sessionId),
    refetchInterval: WATCHTOWER_REFRESH_MS,
    staleTime: 2000,
  })
}

export function useWatchtowerActions() {
  const queryClient = useQueryClient()

  const invalidate = async () => {
    await queryClient.invalidateQueries({ queryKey: ["watchtower"] })
  }

  const revokeSession = useMutation({
    mutationFn: (params: { sessionId: string; reason?: string }) =>
      watchtowerRevokeWatchtowerSession({
        sessionId: params.sessionId,
        requestBody: {
          reason: params.reason,
        },
      }),
    onSuccess: invalidate,
  })

  const disableAgent = useMutation({
    mutationFn: (params: { agentId: string; reason?: string }) =>
      watchtowerDisableWatchtowerAgent({
        agentId: params.agentId,
        requestBody: {
          reason: params.reason,
        },
      }),
    onSuccess: invalidate,
  })

  const enableAgent = useMutation({
    mutationFn: (params: { agentId: string; reason?: string }) =>
      watchtowerEnableWatchtowerAgent({
        agentId: params.agentId,
        requestBody: {
          reason: params.reason,
        },
      }),
    onSuccess: invalidate,
  })

  return {
    revokeSession,
    disableAgent,
    enableAgent,
  }
}

export function defaultWatchtowerAgentId(
  agents: WatchtowerAgentRead[]
): string | null {
  if (agents.length === 0) {
    return null
  }
  return agents[0]?.id ?? null
}
