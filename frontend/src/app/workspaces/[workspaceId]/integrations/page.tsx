"use client"

import { useState } from "react"
import { ProviderMetadata } from "@/client"
import { useWorkspace } from "@/providers/workspace"

import { useIntegrations } from "@/lib/hooks"
import { Button } from "@/components/ui/button"
import { ProviderConfigForm } from "@/components/provider-config-form"

export default function IntegrationsPage() {
  const { workspaceId } = useWorkspace()
  const [isConfigDialogOpen, setIsConfigDialogOpen] = useState(false)
  const [selectedProvider, setSelectedProvider] =
    useState<ProviderMetadata | null>(null)

  const {
    integrations,
    integrationsIsLoading,
    providers,
    providersIsLoading,
    connectProvider,
    connectProviderIsPending,
    disconnectProvider,
    disconnectProviderIsPending,
    configureProviderIsPending,
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

  const openConfigDialog = (provider: ProviderMetadata) => {
    setSelectedProvider(provider)
    setIsConfigDialogOpen(true)
  }

  if (integrationsIsLoading) {
    return <div className="p-6">Loading integrations...</div>
  }

  return (
    <div className="container mx-auto space-y-6 p-6">
      <h1 className="text-2xl font-bold">Integrations</h1>

      {/* Available Providers */}
      <div className="space-y-4">
        <h2 className="text-lg font-semibold">Available Providers</h2>

        {providersIsLoading ? (
          <div className="text-muted-foreground">Loading providers...</div>
        ) : providers?.length === 0 ? (
          <div className="text-muted-foreground">No providers available</div>
        ) : (
          <div className="grid gap-4 md:grid-cols-2 lg:grid-cols-3">
            {providers?.map((provider) => (
              <div key={provider.id} className="rounded-lg border p-4">
                <div className="flex items-start justify-between">
                  <div className="space-y-2">
                    <h3 className="font-medium">{provider.name}</h3>
                    <p className="text-sm text-muted-foreground">
                      {provider.description}
                    </p>
                  </div>
                </div>
                <div className="mt-4 flex gap-2">
                  <Button
                    variant="secondary"
                    size="sm"
                    onClick={() => openConfigDialog(provider)}
                  >
                    Configure
                  </Button>
                  <Button
                    size="sm"
                    onClick={() => handleConnect(provider.id)}
                    disabled={connectProviderIsPending}
                  >
                    {connectProviderIsPending ? "Connecting..." : "Connect"}
                  </Button>
                  <Button
                    size="sm"
                    variant="outline"
                    onClick={() => handleCheckStatus(provider.id)}
                  >
                    Status
                  </Button>
                </div>
              </div>
            ))}
          </div>
        )}

        {/* Provider Configuration Form */}
        {selectedProvider && (
          <ProviderConfigForm
            provider={selectedProvider}
            isOpen={isConfigDialogOpen}
            onClose={() => {
              setIsConfigDialogOpen(false)
              setSelectedProvider(null)
            }}
            isLoading={configureProviderIsPending}
          />
        )}
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
