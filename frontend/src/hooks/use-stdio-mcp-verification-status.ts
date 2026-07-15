import {
  type Query,
  type QueryClient,
  useQueries,
  useQueryClient,
} from "@tanstack/react-query"
import { useEffect, useMemo } from "react"

import {
  type MCPVerificationStatusRead,
  mcpIntegrationsGetMcpIntegrationVerificationStatus,
} from "@/client"
import { toast } from "@/components/ui/use-toast"
import { MCP_STDIO_VERIFICATION_POLL_INTERVAL_MS } from "@/lib/integrations"

const previousVerificationStatuses = new Map<
  string,
  MCPVerificationStatusRead["status"]
>()
const MCP_VERIFICATION_TOAST_DETAIL_MAX_LENGTH = 280

type UseStdioMcpVerificationStatusParams = {
  workspaceId: string
  pendingIntegrationIds: string[]
}

export type StdioMcpVerificationStatuses = ReadonlyMap<
  string,
  MCPVerificationStatusRead
>

/**
 * Polls Temporal-backed stdio MCP verification status for pending integrations.
 */
export function useStdioMcpVerificationStatus({
  workspaceId,
  pendingIntegrationIds,
}: UseStdioMcpVerificationStatusParams): StdioMcpVerificationStatuses {
  const queryClient = useQueryClient()
  const mcpIntegrationIds = useMemo(
    () => Array.from(new Set(pendingIntegrationIds)).sort(),
    [pendingIntegrationIds]
  )

  const statusQueries = useQueries({
    queries: mcpIntegrationIds.map((mcpIntegrationId) => ({
      queryKey: [
        "mcp-verification-status",
        workspaceId,
        mcpIntegrationId,
      ] as const,
      queryFn: async () =>
        await mcpIntegrationsGetMcpIntegrationVerificationStatus({
          workspaceId,
          mcpIntegrationId,
        }),
      enabled: Boolean(workspaceId && mcpIntegrationId),
      refetchInterval: stdioMcpVerificationRefetchInterval,
      refetchOnWindowFocus: false,
    })),
  })

  useEffect(() => {
    for (const [index, query] of statusQueries.entries()) {
      const mcpIntegrationId = mcpIntegrationIds[index]
      const statusRead = query.data
      if (!workspaceId || !mcpIntegrationId || !statusRead) {
        continue
      }

      const key = verificationStatusKey(workspaceId, mcpIntegrationId)
      const previousStatus = previousVerificationStatuses.get(key)
      if (previousStatus === statusRead.status) {
        continue
      }

      previousVerificationStatuses.set(key, statusRead.status)

      if (statusRead.status === "succeeded") {
        invalidateMcpVerificationQueries(
          queryClient,
          workspaceId,
          mcpIntegrationId
        )
        continue
      }

      // A saved failure is also loaded when the page mounts. Only announce a
      // failure that this client observed completing; the persistent failure
      // badge remains responsible for failures loaded after a refresh.
      if (statusRead.status === "failed" && previousStatus === "verifying") {
        toast({
          title: "MCP server verification failed",
          description: verificationFailureToastDescription(statusRead.error),
          variant: "destructive",
        })
      }
    }
  }, [mcpIntegrationIds, queryClient, statusQueries, workspaceId])

  return new Map(
    mcpIntegrationIds.flatMap((mcpIntegrationId, index) => {
      const statusRead = statusQueries[index]?.data
      return statusRead ? [[mcpIntegrationId, statusRead] as const] : []
    })
  )
}

function verificationFailureToastDescription(
  error: string | null | undefined
): string {
  if (!error) {
    return "Stdio MCP verification failed. Open the failure badge for details."
  }

  const meaningfulLine = error
    .split("\n")
    .map((line) => line.trim())
    .reverse()
    .find((line) => line && !isStdioProbeStartupNoise(line))
  if (!meaningfulLine) {
    return "Stdio MCP verification failed. Open the failure badge for details."
  }

  const detail =
    meaningfulLine.length > MCP_VERIFICATION_TOAST_DETAIL_MAX_LENGTH
      ? `${meaningfulLine.slice(0, MCP_VERIFICATION_TOAST_DETAIL_MAX_LENGTH - 1)}…`
      : meaningfulLine
  return `${detail} — Open the failure badge for details.`
}

function isStdioProbeStartupNoise(line: string): boolean {
  return (
    /^(Downloading|Downloaded|Installed)\b/.test(line) ||
    /^AuthlibDeprecationWarning:/.test(line) ||
    /^[╭╮╰╯│─\s]+$/.test(line) ||
    (line.startsWith("│") && line.endsWith("│"))
  )
}

/**
 * Seeds a newly started verification in the query cache and resumes polling.
 *
 * Failed verification queries are terminal and stop polling. A reconnect uses
 * the same integration and query key, so explicitly moving that key back to
 * `verifying` prevents a cached failure from hiding the replacement workflow.
 */
export function markStdioMcpVerificationStarted(
  queryClient: QueryClient,
  workspaceId: string,
  mcpIntegrationId: string
): void {
  const queryKey = [
    "mcp-verification-status",
    workspaceId,
    mcpIntegrationId,
  ] as const
  previousVerificationStatuses.set(
    verificationStatusKey(workspaceId, mcpIntegrationId),
    "verifying"
  )
  queryClient.setQueryData<MCPVerificationStatusRead>(queryKey, {
    status: "verifying",
    error: null,
  })
  void queryClient.invalidateQueries({ queryKey })
}

function verificationStatusKey(
  workspaceId: string,
  mcpIntegrationId: string
): string {
  return `${workspaceId}:${mcpIntegrationId}`
}

function stdioMcpVerificationRefetchInterval(
  query: Query<MCPVerificationStatusRead>
): number | false {
  const status = query.state.data?.status
  if (!status || status === "verifying") {
    return MCP_STDIO_VERIFICATION_POLL_INTERVAL_MS
  }
  return false
}

function invalidateMcpVerificationQueries(
  queryClient: QueryClient,
  workspaceId: string,
  mcpIntegrationId: string
): void {
  void queryClient.invalidateQueries({
    queryKey: ["mcp-integrations", workspaceId],
  })
  void queryClient.invalidateQueries({
    queryKey: ["mcp-integration", workspaceId, mcpIntegrationId],
  })
  void queryClient.invalidateQueries({
    queryKey: ["mcp-catalog", workspaceId],
  })
}
