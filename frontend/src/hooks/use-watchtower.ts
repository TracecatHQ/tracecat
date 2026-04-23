"use client"

import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query"
import {
  type WatchtowerAgentRead,
  type WatchtowerAgentToolCallRead,
  watchtowerDisableWatchtowerAgent,
  watchtowerEnableWatchtowerAgent,
  watchtowerListWatchtowerAgents,
  watchtowerListWatchtowerAgentToolCalls,
} from "@/client"

const WATCHTOWER_REFRESH_MS = 5000

/**
 * Fetch the deduplicated agent list (one row per email + harness).
 */
export function useWatchtowerAgents(params?: {
  status?: WatchtowerAgentRead["status"]
  agentType?: WatchtowerAgentRead["agent_type"]
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

/**
 * Fetch tool calls for a logical agent (the canonical id from the agent list);
 * the backend fans out across duplicate fingerprints in the same group.
 */
export function useWatchtowerAgentToolCalls(
  agentId: string | null,
  params?: {
    status?: WatchtowerAgentToolCallRead["call_status"]
    limit?: number
  }
) {
  const status = params?.status
  const limit = params?.limit ?? 100

  return useQuery({
    queryKey: ["watchtower", "agent-tool-calls", agentId, { status, limit }],
    queryFn: () => {
      if (!agentId) {
        throw new Error("Missing agent ID")
      }
      return watchtowerListWatchtowerAgentToolCalls({
        agentId,
        status,
        limit,
      })
    },
    enabled: Boolean(agentId),
    refetchInterval: WATCHTOWER_REFRESH_MS,
    staleTime: 2000,
  })
}

export function useWatchtowerActions() {
  const queryClient = useQueryClient()

  const invalidate = async () => {
    await queryClient.invalidateQueries({ queryKey: ["watchtower"] })
  }

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
    mutationFn: (params: { agentId: string }) =>
      watchtowerEnableWatchtowerAgent({
        agentId: params.agentId,
      }),
    onSuccess: invalidate,
  })

  return {
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
