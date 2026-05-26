"use client"

import { Check, ChevronDown, ChevronRight, Copy, Loader2 } from "lucide-react"
import { useEffect, useMemo, useState } from "react"
import type { CatalogAuthOption } from "@/client"
import { MultiTagCommandInput } from "@/components/tags-input"
import {
  AlertDialog,
  AlertDialogAction,
  AlertDialogCancel,
  AlertDialogContent,
  AlertDialogDescription,
  AlertDialogFooter,
  AlertDialogHeader,
  AlertDialogTitle,
} from "@/components/ui/alert-dialog"
import { Button } from "@/components/ui/button"
import {
  Dialog,
  DialogContent,
  DialogDescription,
  DialogFooter,
  DialogHeader,
  DialogTitle,
} from "@/components/ui/dialog"
import { Input } from "@/components/ui/input"
import { Label } from "@/components/ui/label"
import { Textarea } from "@/components/ui/textarea"
import { toast } from "@/components/ui/use-toast"
import {
  useDeleteProviderAuthConfig,
  useProviderAuthConfig,
  useTestProviderAuthConnection,
  useUpdateProviderAuthConfig,
} from "@/lib/hooks/integrations-catalog"
import { cn } from "@/lib/utils"

interface ConfigureDialogProps {
  open: boolean
  onOpenChange: (open: boolean) => void
  workspaceId: string
  integrationId: string
  displayName: string
  authOptions: CatalogAuthOption[]
  defaultAuthOption?: CatalogAuthOption | null
}

function optionKey(option: CatalogAuthOption): string {
  return [
    option.auth_method,
    option.provider_id ?? "unknown",
    option.grant_type ?? "none",
  ].join(":")
}

function isConfigurableOption(option: CatalogAuthOption): boolean {
  return Boolean(
    option.enabled !== false &&
      option.provider_id &&
      option.grant_type &&
      option.requires_config
  )
}

function optionStatusLabel(option: CatalogAuthOption): string {
  if (option.status === "connected") {
    return "Connected"
  }
  if (option.status === "configured") {
    return "Configured"
  }
  return "Needs configuration"
}

function fallbackRedirectUrl(): string {
  if (typeof window === "undefined") {
    return "/integrations/callback"
  }
  return `${window.location.origin}/integrations/callback`
}

export function ConfigureDialog({
  open,
  onOpenChange,
  workspaceId,
  integrationId,
  displayName,
  authOptions,
  defaultAuthOption,
}: ConfigureDialogProps) {
  const configurableOptions = useMemo(
    () => authOptions.filter(isConfigurableOption),
    [authOptions]
  )
  const defaultKey = useMemo(() => {
    const preferred = defaultAuthOption
      ? configurableOptions.find(
          (option) => optionKey(option) === optionKey(defaultAuthOption)
        )
      : null
    const fallback = preferred ?? configurableOptions[0]
    return fallback ? optionKey(fallback) : ""
  }, [configurableOptions, defaultAuthOption])
  const [selectedKey, setSelectedKey] = useState(defaultKey)

  useEffect(() => {
    if (open) {
      setSelectedKey(defaultKey)
    }
  }, [defaultKey, open])

  const selectedOption =
    configurableOptions.find((option) => optionKey(option) === selectedKey) ??
    configurableOptions[0]

  return (
    <Dialog open={open} onOpenChange={onOpenChange}>
      <DialogContent className="max-w-xl">
        <DialogHeader>
          <DialogTitle>Configure {displayName}</DialogTitle>
          <DialogDescription>
            Save the provider credentials used by this workspace.
          </DialogDescription>
        </DialogHeader>
        {configurableOptions.length === 0 || !selectedOption ? (
          <div className="space-y-3">
            <p className="text-sm text-muted-foreground">
              This integration does not require workspace configuration.
            </p>
            <DialogFooter>
              <Button variant="outline" onClick={() => onOpenChange(false)}>
                Close
              </Button>
            </DialogFooter>
          </div>
        ) : configurableOptions.length === 1 ? (
          <ProviderConfigForm
            key={optionKey(selectedOption)}
            open={open}
            workspaceId={workspaceId}
            integrationId={integrationId}
            authOption={selectedOption}
            onClose={() => onOpenChange(false)}
          />
        ) : (
          <div className="space-y-4">
            <div className="grid gap-2">
              {configurableOptions.map((option) => {
                const key = optionKey(option)
                const selected = selectedKey === key
                return (
                  <button
                    key={key}
                    type="button"
                    className={cn(
                      "flex items-start gap-3 rounded-md border px-3 py-2 text-left transition-colors",
                      selected
                        ? "border-foreground/50 bg-muted/40"
                        : "border-border hover:border-foreground/30 hover:bg-muted/20"
                    )}
                    onClick={() => setSelectedKey(key)}
                    aria-pressed={selected}
                  >
                    <span className="min-w-0 flex-1">
                      <span className="block text-sm font-medium leading-5">
                        {option.label}
                      </span>
                      {option.description ? (
                        <span className="line-clamp-2 text-xs text-muted-foreground">
                          {option.description}
                        </span>
                      ) : null}
                    </span>
                    <span
                      className={cn(
                        "mt-0.5 rounded border px-1.5 py-0.5 text-[10px] font-medium",
                        option.status === "connected" ||
                          option.status === "configured"
                          ? "border-emerald-400/50 bg-emerald-500/10 text-emerald-700"
                          : "border-amber-400/60 bg-amber-50 text-amber-700"
                      )}
                    >
                      {optionStatusLabel(option)}
                    </span>
                  </button>
                )
              })}
            </div>
            <ProviderConfigForm
              key={optionKey(selectedOption)}
              open={open}
              workspaceId={workspaceId}
              integrationId={integrationId}
              authOption={selectedOption}
              onClose={() => onOpenChange(false)}
            />
          </div>
        )}
      </DialogContent>
    </Dialog>
  )
}

function ProviderConfigForm({
  open,
  workspaceId,
  integrationId,
  authOption,
  onClose,
}: {
  open: boolean
  workspaceId: string
  integrationId: string
  authOption: CatalogAuthOption
  onClose: () => void
}) {
  const providerId = authOption.provider_id!
  const grantType = authOption.grant_type!
  const isServiceAccount = authOption.auth_method === "service_account"
  const canTest =
    authOption.auth_method === "oauth_client_credentials" || isServiceAccount
  const { provider, providerIsLoading, integration, integrationIsLoading } =
    useProviderAuthConfig(workspaceId, authOption, open)
  const { updateProviderAuthConfig, updateProviderAuthConfigIsPending } =
    useUpdateProviderAuthConfig({
      workspaceId,
      integrationId,
      providerId,
      grantType,
    })
  const { deleteProviderAuthConfig, deleteProviderAuthConfigIsPending } =
    useDeleteProviderAuthConfig({
      workspaceId,
      integrationId,
      providerId,
      grantType,
    })
  const { testProviderAuthConnection, testProviderAuthConnectionIsPending } =
    useTestProviderAuthConnection({
      workspaceId,
      integrationId,
      providerId,
      grantType,
    })

  const [clientId, setClientId] = useState("")
  const [clientSecret, setClientSecret] = useState("")
  const [serviceAccountJson, setServiceAccountJson] = useState("")
  const [scopes, setScopes] = useState<string[]>([])
  const [authEndpoint, setAuthEndpoint] = useState("")
  const [tokenEndpoint, setTokenEndpoint] = useState("")
  const [advancedOpen, setAdvancedOpen] = useState(false)
  const [pendingDelete, setPendingDelete] = useState(false)

  useEffect(() => {
    setClientId("")
    setClientSecret("")
    setServiceAccountJson("")
    setScopes(integration?.requested_scopes ?? provider?.scopes.default ?? [])
    setAuthEndpoint(
      integration?.authorization_endpoint ??
        provider?.default_authorization_endpoint ??
        ""
    )
    setTokenEndpoint(
      integration?.token_endpoint ?? provider?.default_token_endpoint ?? ""
    )
    setAdvancedOpen(false)
  }, [authOption, integration, provider])

  const redirectUrl = provider?.redirect_uri ?? fallbackRedirectUrl()
  const hasExistingConfig = Boolean(integration)
  const hasStoredClientId = Boolean(integration?.client_id)
  const canSave = isServiceAccount
    ? hasExistingConfig || Boolean(serviceAccountJson.trim())
    : hasExistingConfig ||
      (Boolean(clientId.trim()) && Boolean(clientSecret.trim()))

  const save = async () => {
    let nextClientId = clientId.trim() || null
    let nextClientSecret = clientSecret.trim() || null

    if (isServiceAccount) {
      const trimmedJson = serviceAccountJson.trim()
      nextClientId = null
      nextClientSecret = trimmedJson || null
      if (trimmedJson) {
        const derived = deriveServiceAccountClientId(trimmedJson)
        if (!derived) return
        nextClientId = derived
      }
    }

    await updateProviderAuthConfig({
      client_id: nextClientId,
      client_secret: nextClientSecret,
      scopes,
      authorization_endpoint: authEndpoint.trim() || null,
      token_endpoint: tokenEndpoint.trim() || null,
    })
    setClientId("")
    setClientSecret("")
    setServiceAccountJson("")
  }

  const confirmDelete = async () => {
    setPendingDelete(false)
    await deleteProviderAuthConfig()
    onClose()
  }

  if (providerIsLoading || integrationIsLoading) {
    return (
      <div className="flex h-32 items-center justify-center">
        <Loader2 className="size-5 animate-spin text-muted-foreground" />
      </div>
    )
  }

  return (
    <div className="space-y-4">
      {authOption.auth_method === "oauth_auth_code" ? (
        <RedirectUrlRow url={redirectUrl} />
      ) : null}

      <div className="space-y-1.5">
        <Label>Scopes</Label>
        <MultiTagCommandInput
          value={scopes}
          onChange={setScopes}
          placeholder="Add scope and press Enter"
          searchKeys={["value"]}
          allowCustomTags
          disableSuggestions
        />
      </div>

      {isServiceAccount ? (
        <div className="space-y-1.5">
          <Label htmlFor="service-account-json">
            Service account JSON
            {hasExistingConfig ? (
              <span className="ml-2 text-xs text-muted-foreground">
                (saved - leave blank to keep)
              </span>
            ) : null}
          </Label>
          <Textarea
            id="service-account-json"
            rows={8}
            className="font-mono text-xs"
            placeholder='{"type": "service_account", ...}'
            value={serviceAccountJson}
            onChange={(event) => setServiceAccountJson(event.target.value)}
          />
        </div>
      ) : (
        <>
          <div className="space-y-1.5">
            <Label htmlFor="client-id">
              Client ID
              {hasStoredClientId ? (
                <span className="ml-2 text-xs text-muted-foreground">
                  (saved - leave blank to keep)
                </span>
              ) : null}
            </Label>
            <Input
              id="client-id"
              placeholder={hasStoredClientId ? "Saved client ID" : "Client ID"}
              value={clientId}
              onChange={(event) => setClientId(event.target.value)}
            />
          </div>

          <div className="space-y-1.5">
            <Label htmlFor="client-secret">
              Client secret
              {hasExistingConfig ? (
                <span className="ml-2 text-xs text-muted-foreground">
                  (saved - leave blank to keep)
                </span>
              ) : null}
            </Label>
            <Input
              id="client-secret"
              type="password"
              placeholder={hasExistingConfig ? "Saved secret" : "Client secret"}
              value={clientSecret}
              onChange={(event) => setClientSecret(event.target.value)}
            />
          </div>
        </>
      )}

      <button
        type="button"
        className="flex items-center gap-1 text-xs font-medium text-muted-foreground hover:text-foreground"
        onClick={() => setAdvancedOpen((prev) => !prev)}
      >
        {advancedOpen ? (
          <ChevronDown className="size-3.5" />
        ) : (
          <ChevronRight className="size-3.5" />
        )}
        Advanced endpoint overrides
      </button>

      {advancedOpen ? (
        <div className="space-y-3 rounded-md border bg-muted/20 p-3">
          {authOption.auth_method === "oauth_auth_code" ? (
            <div className="space-y-1.5">
              <Label htmlFor="auth-endpoint">Authorization URL</Label>
              <Input
                id="auth-endpoint"
                placeholder="https://example.com/oauth/authorize"
                value={authEndpoint}
                onChange={(event) => setAuthEndpoint(event.target.value)}
              />
            </div>
          ) : null}
          <div className="space-y-1.5">
            <Label htmlFor="token-endpoint">Token URL</Label>
            <Input
              id="token-endpoint"
              placeholder="https://example.com/oauth/token"
              value={tokenEndpoint}
              onChange={(event) => setTokenEndpoint(event.target.value)}
            />
          </div>
        </div>
      ) : null}

      <DialogFooter className="gap-2 sm:justify-between">
        {hasExistingConfig ? (
          <Button
            variant="ghost"
            className="text-destructive hover:text-destructive"
            disabled={deleteProviderAuthConfigIsPending}
            onClick={() => setPendingDelete(true)}
          >
            Remove configuration
          </Button>
        ) : (
          <span />
        )}
        <div className="flex gap-2">
          <Button variant="outline" onClick={onClose}>
            Close
          </Button>
          {canTest ? (
            <Button
              variant="outline"
              disabled={
                !hasExistingConfig ||
                updateProviderAuthConfigIsPending ||
                testProviderAuthConnectionIsPending
              }
              onClick={() => testProviderAuthConnection()}
            >
              {testProviderAuthConnectionIsPending ? (
                <Loader2 className="mr-2 size-4 animate-spin" />
              ) : null}
              Test
            </Button>
          ) : null}
          <Button
            onClick={save}
            disabled={!canSave || updateProviderAuthConfigIsPending}
          >
            {updateProviderAuthConfigIsPending ? (
              <Loader2 className="mr-2 size-4 animate-spin" />
            ) : null}
            Save configuration
          </Button>
        </div>
      </DialogFooter>

      <AlertDialog open={pendingDelete} onOpenChange={setPendingDelete}>
        <AlertDialogContent>
          <AlertDialogHeader>
            <AlertDialogTitle>Remove configuration?</AlertDialogTitle>
            <AlertDialogDescription>
              This removes the saved provider credentials. Existing connections
              keep working until they expire, then users will need to reconnect.
            </AlertDialogDescription>
          </AlertDialogHeader>
          <AlertDialogFooter>
            <AlertDialogCancel>Cancel</AlertDialogCancel>
            <AlertDialogAction
              className="bg-destructive text-destructive-foreground hover:bg-destructive/90"
              onClick={confirmDelete}
            >
              Remove
            </AlertDialogAction>
          </AlertDialogFooter>
        </AlertDialogContent>
      </AlertDialog>
    </div>
  )
}

function deriveServiceAccountClientId(json: string): string | null {
  try {
    const parsed = JSON.parse(json) as unknown
    if (!parsed || typeof parsed !== "object") {
      throw new Error("Service account JSON must be an object.")
    }
    const record = parsed as Record<string, unknown>
    const clientEmail = record.client_email
    if (typeof clientEmail === "string" && clientEmail.trim()) {
      return clientEmail.trim()
    }
    const clientId = record.client_id
    if (typeof clientId === "string" && clientId.trim()) {
      return clientId.trim()
    }
  } catch {
    toast({
      title: "Invalid JSON",
      description: "Paste a valid service account JSON key.",
      variant: "destructive",
    })
    return null
  }

  toast({
    title: "Missing service account identity",
    description: "Service account JSON must include client_email or client_id.",
    variant: "destructive",
  })
  return null
}

function RedirectUrlRow({ url }: { url: string }) {
  const [copied, setCopied] = useState(false)

  const copy = async () => {
    try {
      await navigator.clipboard.writeText(url)
      setCopied(true)
      setTimeout(() => setCopied(false), 1500)
    } catch {
      /* noop */
    }
  }

  return (
    <div className="space-y-1.5">
      <Label>Redirect URL</Label>
      <div className="flex items-center gap-2 rounded-md border bg-muted/30 px-2 py-1.5 text-xs">
        <code className="flex-1 truncate font-mono text-foreground/80">
          {url}
        </code>
        <Button
          type="button"
          size="icon"
          variant="ghost"
          className="size-7 shrink-0"
          onClick={copy}
          aria-label="Copy redirect URL"
        >
          {copied ? (
            <Check className="size-3.5" />
          ) : (
            <Copy className="size-3.5" />
          )}
        </Button>
      </div>
    </div>
  )
}
