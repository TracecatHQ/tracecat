"use client"

import { useWorkspace } from "@/providers/workspace"

import { useIntegrations } from "@/lib/hooks"
import { Button } from "@/components/ui/button"

export default function IntegrationsPage() {
  const { workspaceId } = useWorkspace()
  const {
    integrations,
    integrationsIsLoading,
    connectProvider,
    connectProviderIsPending,
    disconnectProvider,
    disconnectProviderIsPending,
    getProviderStatus,
  } = useIntegrations(workspaceId)

  const handleConnect = async (provider: string) => {
    await connectProvider(provider)
  }

  const handleDisconnect = async (provider: string) => {
    await disconnectProvider(provider)
  }

  const handleCheckStatus = async (provider: string) => {
    const status = await getProviderStatus(provider)
    console.log(`Status for ${provider}:`, status)
    alert(`Status for ${provider}: ${JSON.stringify(status, null, 2)}`)
  }

  if (integrationsIsLoading) {
    return <div className="p-6">Loading integrations...</div>
  }

  return (
    <div className="container mx-auto space-y-6 p-6">
      <h1 className="text-2xl font-bold">Integrations Test Page</h1>

      <div className="space-y-4">
        <h2 className="text-lg font-semibold">Test Microsoft Integration</h2>

        <div className="flex gap-4">
          <Button
            onClick={() => handleConnect("microsoft")}
            disabled={connectProviderIsPending}
          >
            {connectProviderIsPending ? "Connecting..." : "Connect Microsoft"}
          </Button>

          <Button
            onClick={() => handleDisconnect("microsoft")}
            disabled={disconnectProviderIsPending}
            variant="destructive"
          >
            {disconnectProviderIsPending
              ? "Disconnecting..."
              : "Disconnect Microsoft"}
          </Button>

          <Button
            onClick={() => handleCheckStatus("microsoft")}
            variant="outline"
          >
            Check Status
          </Button>
        </div>
      </div>

      <div className="space-y-4">
        <h2 className="text-lg font-semibold">Current Integrations</h2>

        {integrations?.length === 0 ? (
          <p className="text-muted-foreground">No integrations connected</p>
        ) : (
          <div className="space-y-2">
            {integrations?.map((integration) => (
              <div key={integration.id} className="rounded-lg border p-4">
                <div className="flex items-center justify-between">
                  <div>
                    <h3 className="font-medium">{integration.provider_id}</h3>
                    <p className="text-sm text-muted-foreground">
                      Token: {integration.token_type}
                      {integration.expires_at && (
                        <span>
                          {" "}
                          | Expires:{" "}
                          {new Date(
                            integration.expires_at
                          ).toLocaleDateString()}
                        </span>
                      )}
                    </p>
                  </div>
                  <Button
                    onClick={() => handleDisconnect(integration.provider_id)}
                    disabled={disconnectProviderIsPending}
                    variant="outline"
                    size="sm"
                  >
                    Disconnect
                  </Button>
                </div>
              </div>
            ))}
          </div>
        )}
      </div>

      <div className="space-y-2 text-sm text-muted-foreground">
        <h3 className="font-medium">Debug Info:</h3>
        <p>• Click &quot;Connect Microsoft&quot; to test OAuth flow</p>
        <p>
          • OAuth callback will redirect to:{" "}
          <code>/integrations/microsoft/callback</code>
        </p>
        <p>• Check browser network tab for API calls</p>
        <p>• Check console for status responses</p>
      </div>
    </div>
  )
}
