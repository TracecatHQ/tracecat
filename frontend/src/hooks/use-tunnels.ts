"use client"

import {
  type ManagedTunnelCreate,
  type ManagedTunnelCreated,
  type TunnelGatewayKeyRead,
  type TunnelRead,
  type TunnelStatusRead,
  type TunnelUpdate,
  tunnelsCreateManagedTunnel,
  tunnelsDeleteTunnel,
  tunnelsGetTunnelStatus,
  tunnelsIssueTunnelGatewayKey,
  tunnelsListTunnels,
  tunnelsUpdateTunnel,
} from "@/client"
import { useMutation, useQuery, useQueryClient } from "@/lib/query"

function tunnelsQueryKey(workspaceId: string) {
  return ["workspace-tunnels", workspaceId] as const
}

/** Lists a workspace's tunnels and exposes the tunnel mutations. */
export function useTunnels(
  workspaceId: string,
  { enabled = true }: { enabled?: boolean } = {}
) {
  const queryClient = useQueryClient()
  const {
    data: response,
    isLoading,
    error,
  } = useQuery({
    queryKey: tunnelsQueryKey(workspaceId),
    queryFn: async () => await tunnelsListTunnels({ workspaceId, limit: 100 }),
    enabled: enabled && Boolean(workspaceId),
  })

  function invalidate() {
    return queryClient.invalidateQueries({
      queryKey: tunnelsQueryKey(workspaceId),
    })
  }

  const createManaged = useMutation<
    ManagedTunnelCreated,
    Error,
    ManagedTunnelCreate
  >({
    mutationFn: async (requestBody) =>
      await tunnelsCreateManagedTunnel({ workspaceId, requestBody }),
    onSuccess: invalidate,
  })

  const update = useMutation<
    TunnelRead,
    Error,
    { tunnelId: string; requestBody: TunnelUpdate }
  >({
    mutationFn: async ({ tunnelId, requestBody }) =>
      await tunnelsUpdateTunnel({ workspaceId, tunnelId, requestBody }),
    onSuccess: invalidate,
  })

  const remove = useMutation<void, Error, string>({
    mutationFn: async (tunnelId) =>
      await tunnelsDeleteTunnel({ workspaceId, tunnelId }),
    onSuccess: invalidate,
  })

  const issueGatewayKey = useMutation<TunnelGatewayKeyRead, Error, string>({
    mutationFn: async (tunnelId) =>
      await tunnelsIssueTunnelGatewayKey({ workspaceId, tunnelId }),
    onSuccess: invalidate,
  })

  return {
    tunnels: response?.items ?? [],
    isLoading,
    error,
    createManagedTunnel: createManaged.mutateAsync,
    createPending: createManaged.isPending,
    updateTunnel: update.mutateAsync,
    updatePending: update.isPending,
    deleteTunnel: remove.mutateAsync,
    deletePending: remove.isPending,
    issueGatewayKey: issueGatewayKey.mutateAsync,
    issueGatewayKeyPending: issueGatewayKey.isPending,
  }
}

/** Polls the live status of one tunnel's site and gateway. */
export function useTunnelStatus(
  workspaceId: string,
  tunnelId: string,
  { enabled = true }: { enabled?: boolean } = {}
) {
  return useQuery<TunnelStatusRead, Error>({
    queryKey: [...tunnelsQueryKey(workspaceId), tunnelId, "status"],
    queryFn: async () =>
      await tunnelsGetTunnelStatus({ workspaceId, tunnelId }),
    enabled,
    refetchInterval: 30_000,
    retry: false,
  })
}
