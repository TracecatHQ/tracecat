"use client"

import { useState } from "react"
import { useWorkspace } from "@/providers/workspace"

import { useIntegrations } from "@/lib/hooks"
import { Button } from "@/components/ui/button"
import {
  Dialog,
  DialogContent,
  DialogDescription,
  DialogFooter,
  DialogHeader,
  DialogTitle,
  DialogTrigger,
} from "@/components/ui/dialog"
import { Input } from "@/components/ui/input"
import { Label } from "@/components/ui/label"

export default function IntegrationsPage() {
  const { workspaceId } = useWorkspace()
  const [isConfigDialogOpen, setIsConfigDialogOpen] = useState(false)
  const [clientId, setClientId] = useState("")
  const [clientSecret, setClientSecret] = useState("")

  const {
    integrations,
    integrationsIsLoading,
    connectProvider,
    connectProviderIsPending,
    disconnectProvider,
    disconnectProviderIsPending,
    configureProvider,
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

  const handleConfigure = async (provider: string) => {
    if (!clientId.trim() || !clientSecret.trim()) {
      alert("Please enter both client ID and client secret")
      return
    }

    try {
      await configureProvider({
        providerId: provider,
        config: {
          client_id: clientId.trim(),
          client_secret: clientSecret.trim(),
        },
      })
      setIsConfigDialogOpen(false)
      setClientId("")
      setClientSecret("")
    } catch (error) {
      console.error("Failed to configure provider:", error)
    }
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
          <Dialog
            open={isConfigDialogOpen}
            onOpenChange={setIsConfigDialogOpen}
          >
            <DialogTrigger asChild>
              <Button variant="secondary">Configure Microsoft</Button>
            </DialogTrigger>
            <DialogContent>
              <DialogHeader>
                <DialogTitle>Configure Microsoft OAuth</DialogTitle>
                <DialogDescription>
                  Enter your Microsoft OAuth application credentials. You can
                  create these in the Azure Portal.
                </DialogDescription>
              </DialogHeader>
              <div className="grid gap-4 py-4">
                <div className="grid grid-cols-4 items-center gap-4">
                  <Label htmlFor="clientId" className="text-right">
                    Client ID
                  </Label>
                  <Input
                    id="clientId"
                    value={clientId}
                    onChange={(e) => setClientId(e.target.value)}
                    className="col-span-3"
                    placeholder="Enter client ID"
                  />
                </div>
                <div className="grid grid-cols-4 items-center gap-4">
                  <Label htmlFor="clientSecret" className="text-right">
                    Client Secret
                  </Label>
                  <Input
                    id="clientSecret"
                    type="password"
                    value={clientSecret}
                    onChange={(e) => setClientSecret(e.target.value)}
                    className="col-span-3"
                    placeholder="Enter client secret"
                  />
                </div>
              </div>
              <DialogFooter>
                <Button
                  variant="outline"
                  onClick={() => {
                    setIsConfigDialogOpen(false)
                    setClientId("")
                    setClientSecret("")
                  }}
                >
                  Cancel
                </Button>
                <Button
                  onClick={() => handleConfigure("microsoft")}
                  disabled={configureProviderIsPending}
                >
                  {configureProviderIsPending
                    ? "Configuring..."
                    : "Save Configuration"}
                </Button>
              </DialogFooter>
            </DialogContent>
          </Dialog>

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
