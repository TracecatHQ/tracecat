"use client"

import { zodResolver } from "@hookform/resolvers/zod"
import { Info, Save } from "lucide-react"
import { useCallback, useMemo } from "react"
import { useForm, useWatch } from "react-hook-form"
import { z } from "zod"
import type { IntegrationUpdate, ProviderRead } from "@/client"
import { MultiTagCommandInput } from "@/components/tags-input"
import { Alert, AlertDescription } from "@/components/ui/alert"
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

const renderHelpContent = (help: EndpointHelp) => {
  if (help == null) {
    return null
  }
  const raw = Array.isArray(help) ? help.join("\n") : help
  const sanitized = raw
    .split("\n")
    .map((line) => line.trimEnd())
    .join("\n")
    .trim()

  if (sanitized.length === 0) {
    return null
  }
  const blocks = sanitized.split(/\n{2,}/)
  return blocks.length > 1 ? (
    <div className="space-y-2 text-left whitespace-pre-line">
      {blocks.map((block, index) => (
        <p key={`help-block-${index}`}>{block}</p>
      ))}
    </div>
  ) : (
    <p className="text-left whitespace-pre-line">{sanitized}</p>
  )
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

  const hasAuthHelp = hasHelpContent(providerAuthHelp)
  const hasTokenHelp = hasHelpContent(providerTokenHelp)

  const currentScopes = integration?.requested_scopes ?? []
  const defaultScopesList = useMemo(
    () => (defaultScopes ?? []).filter((scope) => scope.trim().length > 0),
    [defaultScopes]
  )
  const watchedScopes = useWatch({
    control: form.control,
    name: "scopes",
  })
  const scopesValue = watchedScopes ?? []
  const normalizedDefaultScopes = useMemo(
    () => [...defaultScopesList].sort(),
    [defaultScopesList]
  )
  const defaultScopeSuggestions = useMemo(
    () =>
      defaultScopesList.map((scope) => ({
        id: scope,
        label: scope,
        value: scope,
      })),
    [defaultScopesList]
  )
  const normalizedScopesValue = useMemo(
    () => [...scopesValue].sort(),
    [scopesValue]
  )
  const isAtDefaultScopes = useMemo(() => {
    if (normalizedDefaultScopes.length === 0) {
      return normalizedScopesValue.length === 0
    }
    if (normalizedDefaultScopes.length !== normalizedScopesValue.length) {
      return false
    }
    return normalizedDefaultScopes.every(
      (scope, index) => scope === normalizedScopesValue[index]
    )
  }, [normalizedDefaultScopes, normalizedScopesValue])

  const watchedAuthEndpoint = useWatch({
    control: form.control,
    name: "authorization_endpoint",
  })
  const watchedTokenEndpoint = useWatch({
    control: form.control,
    name: "token_endpoint",
  })
  const authEndpointValue = watchedAuthEndpoint ?? ""
  const tokenEndpointValue = watchedTokenEndpoint ?? ""
  const defaultAuthEndpoint = providerDefaultAuth ?? ""
  const defaultTokenEndpoint = providerDefaultToken ?? ""
  const isAtDefaultEndpoints = useMemo(() => {
    return (
      authEndpointValue.trim() === defaultAuthEndpoint.trim() &&
      tokenEndpointValue.trim() === defaultTokenEndpoint.trim()
    )
  }, [
    authEndpointValue,
    tokenEndpointValue,
    defaultAuthEndpoint,
    defaultTokenEndpoint,
  ])

  const handleResetScopes = useCallback(() => {
    const nextValue = defaultScopesList.length > 0 ? [...defaultScopesList] : []
    form.setValue("scopes", nextValue, {
      shouldDirty: !isAtDefaultScopes,
      shouldTouch: true,
    })
    form.clearErrors("scopes")
    void form.trigger("scopes")
  }, [defaultScopesList, form, isAtDefaultScopes])

  const handleResetEndpoints = useCallback(() => {
    form.setValue("authorization_endpoint", defaultAuthEndpoint, {
      shouldDirty: !isAtDefaultEndpoints,
      shouldTouch: true,
    })
    form.setValue("token_endpoint", defaultTokenEndpoint, {
      shouldDirty: !isAtDefaultEndpoints,
      shouldTouch: true,
    })
    form.clearErrors("authorization_endpoint")
    form.clearErrors("token_endpoint")
    void form.trigger("authorization_endpoint")
    void form.trigger("token_endpoint")
  }, [defaultAuthEndpoint, defaultTokenEndpoint, form, isAtDefaultEndpoints])

  if (integrationIsLoading) {
    return <ProviderConfigFormSkeleton />
  }

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
                Requested scopes
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
              <div className="flex items-center justify-between gap-2">
                <CardTitle>Endpoints</CardTitle>
                {(defaultAuthEndpoint.length > 0 ||
                  defaultTokenEndpoint.length > 0 ||
                  authEndpointValue.length > 0 ||
                  tokenEndpointValue.length > 0) && (
                  <Button
                    type="button"
                    variant="link"
                    size="sm"
                    className="px-0"
                    onClick={handleResetEndpoints}
                    disabled={isAtDefaultEndpoints}
                  >
                    Reset endpoints
                  </Button>
                )}
              </div>
            </CardHeader>
            <CardContent className="flex flex-col gap-4">
              {!isMCP && (hasAuthHelp || hasTokenHelp) && (
                <Alert>
                  <Info className="h-4 w-4" />
                  <AlertDescription className="text-xs">
                    {hasAuthHelp && (
                      <div>
                        <div className="font-medium mb-1">
                          Authorization endpoint:
                        </div>
                        {renderHelpContent(providerAuthHelp)}
                      </div>
                    )}
                    {hasAuthHelp && hasTokenHelp && <div className="mt-3" />}
                    {hasTokenHelp && (
                      <div>
                        <div className="font-medium mb-1">Token endpoint:</div>
                        {renderHelpContent(providerTokenHelp)}
                      </div>
                    )}
                  </AlertDescription>
                </Alert>
              )}
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
                    <FormDescription className="text-xs">
                      Initiates the OAuth consent flow with the provider. Keep
                      the default unless a custom authorization URL is required.
                    </FormDescription>
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
                    <FormDescription className="text-xs">
                      Exchanges authorization codes for tokens at the provider.
                      Keep the default unless a different token URL is required.
                    </FormDescription>
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
              <div className="flex flex-col gap-2">
                <div className="flex items-center gap-2">
                  <span className="text-sm font-medium text-muted-foreground">
                    Default scopes
                  </span>
                </div>
                {defaultScopesList.length ? (
                  <div className="flex flex-wrap gap-1">
                    {defaultScopesList.map((scope) => (
                      <Badge
                        key={scope}
                        variant="secondary"
                        className="text-xs"
                      >
                        {scope}
                      </Badge>
                    ))}
                  </div>
                ) : (
                  <span className="text-xs text-muted-foreground">
                    No default scopes provided.
                  </span>
                )}
              </div>

              <FormField
                control={form.control}
                name="scopes"
                render={({ field }) => (
                  <FormItem>
                    <div className="flex items-center justify-between gap-2">
                      <FormLabel>OAuth scopes</FormLabel>
                      {(defaultScopesList.length > 0 ||
                        scopesValue.length > 0) && (
                        <Button
                          type="button"
                          variant="link"
                          size="sm"
                          className="px-0"
                          onClick={handleResetScopes}
                          disabled={isAtDefaultScopes}
                        >
                          Reset scopes
                        </Button>
                      )}
                    </div>
                    <FormControl>
                      <MultiTagCommandInput
                        value={field.value ?? []}
                        onChange={field.onChange}
                        suggestions={defaultScopeSuggestions}
                        searchKeys={["label", "value"]}
                        allowCustomTags
                        placeholder="Add scopes"
                      />
                    </FormControl>
                    <FormMessage />
                    <FormDescription className="text-xs">
                      Configure the OAuth scopes for this integration.
                    </FormDescription>
                  </FormItem>
                )}
              />
            </CardContent>
          </Card>
          <div className="flex flex-wrap items-center gap-3">
            <Button
              type="submit"
              variant={
                integration?.status === "configured" ? "outline" : "default"
              }
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
