"use client"

import { useMutation, useQueryClient } from "@tanstack/react-query"
import type { OAuthGrantType } from "@/client"
import {
  integrationsConnectProvider,
  integrationsDisconnectIntegration,
  integrationsTestConnection,
} from "@/client"
import { toast } from "@/components/ui/use-toast"
import type { TracecatApiError } from "@/lib/errors"
import { integrationKeys } from "@/lib/integrations"

interface ProviderRef {
  providerId: string
  grantType: OAuthGrantType
}

/**
 * Return an invalidator that refreshes every query that can change when an
 * OAuth integration's connection state changes.
 *
 * Connect/disconnect/test all flip the same conceptual surface — the
 * provider's `integration_status`, the OAuth integration row, the MCP
 * integrations list (since platform MCP rows are auto-derived from MCP OAuth
 * providers). Centralized here so call sites don't drift apart on what to
 * invalidate.
 */
function useInvalidateIntegrationQueries(workspaceId: string) {
  const queryClient = useQueryClient()
  return ({ providerId, grantType }: ProviderRef) => {
    queryClient.invalidateQueries({
      queryKey: integrationKeys.integration(providerId, workspaceId, grantType),
    })
    queryClient.invalidateQueries({
      queryKey: integrationKeys.providers(workspaceId),
    })
    queryClient.invalidateQueries({
      queryKey: integrationKeys.integrations(workspaceId),
    })
    queryClient.invalidateQueries({
      queryKey: ["mcp-integrations", workspaceId],
    })
  }
}

/**
 * Start an OAuth authorization flow for the given provider.
 *
 * On success the browser is redirected to the provider's auth URL — the
 * mutation never resolves visibly because navigation happens first.
 */
export function useConnectProvider(workspaceId: string) {
  return useMutation({
    mutationFn: async ({ providerId }: Pick<ProviderRef, "providerId">) =>
      await integrationsConnectProvider({ providerId, workspaceId }),
    onSuccess: (result) => {
      window.location.href = result.auth_url
    },
    onError: (error: TracecatApiError) => {
      toast({
        title: "Failed to start OAuth",
        description: String(error.body?.detail ?? error.message),
        variant: "destructive",
      })
    },
  })
}

/**
 * Disconnect a connected OAuth integration. Invalidates all related queries
 * on success so dependent UI flips back to the not-connected state.
 */
export function useDisconnectProvider(workspaceId: string) {
  const invalidate = useInvalidateIntegrationQueries(workspaceId)
  return useMutation({
    mutationFn: async ({ providerId, grantType }: ProviderRef) =>
      await integrationsDisconnectIntegration({
        providerId,
        workspaceId,
        grantType,
      }),
    onSuccess: (_, variables) => {
      invalidate(variables)
      toast({
        title: "Disconnected",
        description: "Successfully disconnected from provider",
      })
    },
    onError: (error: TracecatApiError) => {
      toast({
        title: "Failed to disconnect",
        description: `${error.body?.detail ?? error.message}`,
        variant: "destructive",
      })
    },
  })
}

/**
 * Test that a configured integration's credentials still work. Used both as
 * a verification action and as a "reconnect" for client-credentials grants
 * that have no interactive OAuth flow.
 */
export function useTestProvider(workspaceId: string) {
  const invalidate = useInvalidateIntegrationQueries(workspaceId)
  return useMutation({
    mutationFn: async ({ providerId }: ProviderRef) =>
      await integrationsTestConnection({ providerId, workspaceId }),
    onSuccess: (result, variables) => {
      invalidate(variables)
      if (result.success) {
        toast({
          title: "Connection successful",
          description: result.message,
        })
      } else {
        toast({
          title: "Connection failed",
          description: result.error || result.message,
          variant: "destructive",
        })
      }
    },
    onError: (error: TracecatApiError) => {
      toast({
        title: "Test failed",
        description: `${error.body?.detail ?? error.message}`,
        variant: "destructive",
      })
    },
  })
}
