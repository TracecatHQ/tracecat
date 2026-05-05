"use client"

import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query"
import {
  type MCPPersonalAccessTokenCreate,
  type MCPPersonalAccessTokenIssueResponse,
  mcpPersonalAccessTokensCreateMcpPersonalAccessToken,
  mcpPersonalAccessTokensListMcpPersonalAccessTokens,
  mcpPersonalAccessTokensRevokeMcpPersonalAccessToken,
} from "@/client"

function workspaceMcpTokensQueryKey(workspaceId: string) {
  return ["workspace-mcp-personal-access-tokens", workspaceId] as const
}

/**
 * Manage workspace-scoped MCP personal access tokens.
 */
export function useWorkspaceMcpPersonalAccessTokens(
  workspaceId: string,
  { enabled = true }: { enabled?: boolean } = {}
) {
  const queryClient = useQueryClient()

  const {
    data: response,
    isLoading,
    error,
  } = useQuery({
    queryKey: workspaceMcpTokensQueryKey(workspaceId),
    queryFn: async () =>
      await mcpPersonalAccessTokensListMcpPersonalAccessTokens({
        workspaceId,
        limit: 100,
      }),
    enabled: enabled && Boolean(workspaceId),
  })

  function invalidate() {
    return queryClient.invalidateQueries({
      queryKey: workspaceMcpTokensQueryKey(workspaceId),
    })
  }

  const { mutateAsync: createToken, isPending: createPending } = useMutation({
    mutationFn: async (
      requestBody: MCPPersonalAccessTokenCreate
    ): Promise<MCPPersonalAccessTokenIssueResponse> =>
      await mcpPersonalAccessTokensCreateMcpPersonalAccessToken({
        workspaceId,
        requestBody,
      }),
    onSuccess: invalidate,
  })

  const { mutateAsync: revokeToken, isPending: revokePending } = useMutation({
    mutationFn: async (tokenId: string) =>
      await mcpPersonalAccessTokensRevokeMcpPersonalAccessToken({
        workspaceId,
        tokenId,
      }),
    onSuccess: invalidate,
  })

  return {
    tokens: response?.items ?? [],
    nextCursor: response?.next_cursor ?? null,
    isLoading,
    error,
    createToken,
    createPending,
    revokeToken,
    revokePending,
  }
}
