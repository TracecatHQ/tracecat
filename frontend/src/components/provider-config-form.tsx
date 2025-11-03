"use client"

import { zodResolver } from "@hookform/resolvers/zod"
import { Info, Save } from "lucide-react"
import { useCallback, useMemo } from "react"
import { useForm } from "react-hook-form"
import { z } from "zod"
import type { IntegrationUpdate, ProviderRead } from "@/client"
import { MultiTagCommandInput } from "@/components/tags-input"
import { Badge } from "@/components/ui/badge"
import { Button } from "@/components/ui/button"
import { Card, CardContent, CardHeader, CardTitle } from "@/components/ui/card"
import {
  Form,
  FormControl,
  FormDescription,
  FormField,
  FormItem,
  FormLabel,
  FormMessage,
} from "@/components/ui/form"
import { Input } from "@/components/ui/input"
import { useIntegrationProvider } from "@/lib/hooks"
import { isMCPProvider } from "@/lib/providers"
import { useWorkspaceId } from "@/providers/workspace-id"

type EndpointHelp = ProviderRead["authorization_endpoint_help"]

const hasHelpContent = (help: EndpointHelp): boolean => {
  if (help == null) {
    return false
  }
  if (Array.isArray(help)) {
    return help.some((item) => item.trim().length > 0)
  }
  return help.trim().length > 0
}

const helpsAreEqual = (a: EndpointHelp, b: EndpointHelp): boolean => {
  if (a === b) {
    return true
  }
  if (a == null || b == null) {
    return false
  }
  if (Array.isArray(a) && Array.isArray(b)) {
    if (a.length !== b.length) {
      return false
    }
    return a.every((value, index) => value === b[index])
  }
  return false
}

const renderHelpContent = (help: EndpointHelp) => {
  if (help == null) {
    return null
  }
  if (Array.isArray(help)) {
    const items = help.filter((item) => item.trim().length > 0)
    if (items.length === 0) {
      return null
    }
    return (
      <ul className="ml-5 list-disc space-y-1 text-xs text-muted-foreground">
        {items.map((item, index) => (
          <li key={`${index}-${item}`}>{item}</li>
        ))}
      </ul>
    )
  }
  return <p className="text-xs text-muted-foreground">{help}</p>
}

const oauthSchema = z.object({
  client_id: z
    .string()
    .trim()
    .max(512, { message: "Client ID must be 512 characters or less" })
    .optional(),
  client_secret: z
    .string()
    .trim()
    .max(512, { message: "Client secret must be 512 characters or less" })
    .optional(),
  scopes: z.array(z.string().trim().min(1)).optional(),
  authorization_endpoint: z
    .string()
    .trim()
    .url({ message: "Enter a valid HTTPS URL" }),
  token_endpoint: z.string().trim().url({ message: "Enter a valid HTTPS URL" }),
})

type OAuthSchema = z.infer<typeof oauthSchema>

interface ProviderConfigFormProps {
  provider: ProviderRead
  onSuccess?: () => void
  additionalButtons?: React.ReactNode
}

export function ProviderConfigForm({
  provider,
  onSuccess,
  additionalButtons,
}: ProviderConfigFormProps) {
  const workspaceId = useWorkspaceId()
  const isMCP = isMCPProvider(provider)
  const {
    metadata: { id },
    scopes: { default: defaultScopes },
    grant_type: grantType,
    default_authorization_endpoint: providerDefaultAuth,
    default_token_endpoint: providerDefaultToken,
    authorization_endpoint_help: providerAuthHelp,
    token_endpoint_help: providerTokenHelp,
  } = provider

  const {
    integration,
    integrationIsLoading,
    updateIntegration,
    updateIntegrationIsPending,
  } = useIntegrationProvider({
    providerId: id,
    workspaceId,
    grantType,
  })

  const defaultValues = useMemo<OAuthSchema>(() => {
    const fallbackScopes = integration?.requested_scopes ?? defaultScopes ?? []
    return {
      client_id: integration?.client_id ?? "",
      client_secret: "",
      scopes: fallbackScopes,
      authorization_endpoint:
        integration?.authorization_endpoint ?? providerDefaultAuth ?? "",
      token_endpoint: integration?.token_endpoint ?? providerDefaultToken ?? "",
    }
  }, [integration, defaultScopes, providerDefaultAuth, providerDefaultToken])

  const form = useForm<OAuthSchema>({
    resolver: zodResolver(oauthSchema),
    defaultValues,
  })

  const onSubmit = useCallback(
    async (data: OAuthSchema) => {
      const params: IntegrationUpdate = {
        client_id: data.client_id?.trim() || undefined,
        client_secret: data.client_secret?.trim() || undefined,
        scopes: data.scopes?.length ? data.scopes : undefined,
        authorization_endpoint: data.authorization_endpoint,
        token_endpoint: data.token_endpoint,
        grant_type: grantType,
      }

      await updateIntegration(params)
      onSuccess?.()
    },
    [grantType, onSuccess, updateIntegration]
  )

  if (integrationIsLoading) {
    return <ProviderConfigFormSkeleton />
  }

  const hasAuthHelp = hasHelpContent(providerAuthHelp)
  const hasTokenHelp = hasHelpContent(providerTokenHelp)
  const helpValuesMatch = helpsAreEqual(providerAuthHelp, providerTokenHelp)
  const scopedHelp =
    !isMCP && (hasAuthHelp || hasTokenHelp) ? (
      <div className="rounded-md border border-dashed border-primary/30 bg-primary/5 p-3 text-xs text-muted-foreground">
        <div className="flex items-start gap-2">
          <Info className="mt-0.5 h-4 w-4" />
          <div className="space-y-2">
            {hasAuthHelp && renderHelpContent(providerAuthHelp)}
            {hasTokenHelp &&
              !helpValuesMatch &&
              renderHelpContent(providerTokenHelp)}
          </div>
        </div>
      </div>
    ) : null

  const currentScopes = integration?.requested_scopes ?? []

  return (
    <div className="flex flex-col gap-6">
      {integration && (
        <Card className="bg-muted/40">
          <CardHeader>
            <CardTitle>Current configuration</CardTitle>
          </CardHeader>
          <CardContent className="flex flex-col gap-4 text-sm">
            <div className="grid grid-cols-1 gap-4 md:grid-cols-2">
              <InfoRow label="Client ID">
                {integration.client_id ? (
                  <span className="font-mono">
                    {integration.client_id.slice(0, 6)}****
                    {integration.client_id.length > 10
                      ? integration.client_id.slice(-4)
                      : ""}
                  </span>
                ) : (
                  <span className="text-muted-foreground">Not configured</span>
                )}
              </InfoRow>
              <InfoRow label="Client secret">
                {integration.status !== "not_configured"
                  ? "Configured"
                  : "Not configured"}
              </InfoRow>
              <InfoRow label="Authorization endpoint">
                {integration.authorization_endpoint ?? "Not configured"}
              </InfoRow>
              <InfoRow label="Token endpoint">
                {integration.token_endpoint ?? "Not configured"}
              </InfoRow>
            </div>
            <div className="flex flex-col gap-2">
              <span className="font-medium text-muted-foreground">
                OAuth scopes
              </span>
              {currentScopes.length ? (
                <div className="flex flex-wrap gap-1">
                  {currentScopes.map((scope) => (
                    <Badge key={scope} variant="outline" className="text-xs">
                      {scope}
                    </Badge>
                  ))}
                </div>
              ) : (
                <span className="text-xs text-muted-foreground">
                  No scopes configured
                </span>
              )}
            </div>
          </CardContent>
        </Card>
      )}

      <Form {...form}>
        <form
          onSubmit={form.handleSubmit(onSubmit)}
          className="flex flex-col gap-6"
        >
          <Card>
            <CardHeader>
              <CardTitle>Client credentials</CardTitle>
            </CardHeader>
            <CardContent className="flex flex-col gap-4">
              <FormField
                control={form.control}
                name="client_id"
                render={({ field }) => (
                  <FormItem>
                    <FormLabel>Client ID</FormLabel>
                    <FormControl>
                      <Input
                        {...field}
                        value={field.value ?? ""}
                        placeholder="Enter client ID"
                      />
                    </FormControl>
                    <FormMessage />
                    <FormDescription className="text-xs">
                      The OAuth application's client identifier. Leave blank to
                      remove stored credentials.
                    </FormDescription>
                  </FormItem>
                )}
              />

              <FormField
                control={form.control}
                name="client_secret"
                render={({ field }) => (
                  <FormItem>
                    <FormLabel>Client secret</FormLabel>
                    <FormControl>
                      <Input
                        {...field}
                        type="password"
                        value={field.value ?? ""}
                        placeholder="Enter client secret"
                      />
                    </FormControl>
                    <FormMessage />
                    <FormDescription className="text-xs">
                      Add or rotate the OAuth client secret. Submit an empty
                      value to keep the existing secret unchanged.
                    </FormDescription>
                  </FormItem>
                )}
              />
            </CardContent>
          </Card>

          <Card>
            <CardHeader>
              <CardTitle>OAuth endpoints</CardTitle>
            </CardHeader>
            <CardContent className="flex flex-col gap-4">
              {scopedHelp}

              <FormField
                control={form.control}
                name="authorization_endpoint"
                render={({ field }) => (
                  <FormItem>
                    <FormLabel>Authorization endpoint</FormLabel>
                    <FormControl>
                      <Input
                        {...field}
                        value={field.value ?? ""}
                        placeholder={providerDefaultAuth ?? "https://..."}
                      />
                    </FormControl>
                    <FormMessage />
                    {providerDefaultAuth && (
                      <FormDescription className="text-xs">
                        Default: {providerDefaultAuth}
                      </FormDescription>
                    )}
                  </FormItem>
                )}
              />

              <FormField
                control={form.control}
                name="token_endpoint"
                render={({ field }) => (
                  <FormItem>
                    <FormLabel>Token endpoint</FormLabel>
                    <FormControl>
                      <Input
                        {...field}
                        value={field.value ?? ""}
                        placeholder={providerDefaultToken ?? "https://..."}
                      />
                    </FormControl>
                    <FormMessage />
                    {providerDefaultToken && (
                      <FormDescription className="text-xs">
                        Default: {providerDefaultToken}
                      </FormDescription>
                    )}
                  </FormItem>
                )}
              />
            </CardContent>
          </Card>

          <Card>
            <CardHeader>
              <CardTitle>Scopes</CardTitle>
            </CardHeader>
            <CardContent className="flex flex-col gap-4">
              <FormField
                control={form.control}
                name="scopes"
                render={({ field }) => (
                  <FormItem>
                    <FormLabel>Requested scopes</FormLabel>
                    <FormControl>
                      <MultiTagCommandInput
                        value={field.value ?? []}
                        onChange={field.onChange}
                        availableOptions={new Set(defaultScopes ?? [])}
                        placeholder="Add scopes"
                      />
                    </FormControl>
                    <FormMessage />
                    <FormDescription className="text-xs">
                      Customize the scopes requested during OAuth. Leave empty
                      to use defaults.
                    </FormDescription>
                  </FormItem>
                )}
              />
            </CardContent>
          </Card>

          {isMCP && (
            <Card className="border-warning">
              <CardContent className="flex gap-2 py-4 text-sm text-warning">
                <Info className="mt-0.5 h-4 w-4" />
                <p>
                  Hosted MCP providers support dynamic client registration.
                  Leave the client credentials blank to register automatically.
                </p>
              </CardContent>
            </Card>
          )}

          <div className="flex flex-wrap items-center gap-3">
            <Button
              type="submit"
              className="gap-2"
              disabled={updateIntegrationIsPending}
            >
              <Save className="h-4 w-4" />
              Save configuration
            </Button>
            {additionalButtons}
          </div>
        </form>
      </Form>
    </div>
  )
}

function InfoRow({
  label,
  children,
}: {
  label: string
  children: React.ReactNode
}) {
  return (
    <div className="flex flex-col gap-1">
      <span className="font-medium text-muted-foreground">{label}</span>
      <span className="text-xs text-foreground break-all">{children}</span>
    </div>
  )
}

export function ProviderConfigFormSkeleton() {
  return (
    <Card className="animate-pulse">
      <CardContent className="flex flex-col gap-4 p-6">
        <div className="h-7 w-40 rounded-md bg-muted" />
        <div className="h-8 w-full rounded-md bg-muted" />
        <div className="h-8 w-full rounded-md bg-muted" />
        <div className="h-8 w-full rounded-md bg-muted" />
        <div className="mt-4 flex gap-3">
          <div className="h-10 w-32 rounded-md bg-muted" />
          <div className="h-10 w-24 rounded-md bg-muted" />
        </div>
      </CardContent>
    </Card>
  )
}
