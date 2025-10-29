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
import {
  Card,
  CardContent,
  CardDescription,
  CardHeader,
  CardTitle,
} from "@/components/ui/card"
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
import { Label } from "@/components/ui/label"
import {
  Tooltip,
  TooltipContent,
  TooltipProvider,
  TooltipTrigger,
} from "@/components/ui/tooltip"
import { useIntegrationProvider } from "@/lib/hooks"
import { useWorkspaceId } from "@/providers/workspace-id"

interface ProviderConfigFormProps {
  provider: ProviderRead
  onSuccess?: () => void
  additionalButtons?: React.ReactNode
}

type IntegrationUpdatePayload = IntegrationUpdate & {
  authorization_endpoint?: string
  token_endpoint?: string
}

type HelpSegment =
  | { type: "paragraph"; content: string }
  | { type: "list"; items: string[] }

const parseHelpText = (text: string): HelpSegment[] => {
  const segments: HelpSegment[] = []
  const lines = text.split(/\r?\n/)
  let currentParagraph: string[] = []
  let currentList: string[] = []

  const flushParagraph = () => {
    if (currentParagraph.length === 0) {
      return
    }
    segments.push({
      type: "paragraph",
      content: currentParagraph.join(" "),
    })
    currentParagraph = []
  }

  const flushList = () => {
    if (currentList.length === 0) {
      return
    }
    segments.push({
      type: "list",
      items: currentList,
    })
    currentList = []
  }

  for (const rawLine of lines) {
    const trimmedLine = rawLine.trim()

    if (!trimmedLine) {
      flushParagraph()
      flushList()
      continue
    }

    const listMatch = trimmedLine.match(/^[-*]\s+(.*)$/)
    if (listMatch) {
      flushParagraph()
      currentList.push(listMatch[1].trim())
      continue
    }

    flushList()
    currentParagraph.push(trimmedLine)
  }

  flushParagraph()
  flushList()

  return segments
}

function ProviderHelpDescription({
  text,
}: {
  text?: string | null
}): JSX.Element | null {
  if (!text) {
    return null
  }

  const segments = parseHelpText(text)

  if (segments.length === 0) {
    return <>{text}</>
  }

  return (
    <div className="space-y-2">
      {segments.map((segment, index) => {
        if (segment.type === "list") {
          return (
            <ul key={`help-list-${index}`} className="list-disc pl-5 space-y-1">
              {segment.items.map((item, itemIndex) => (
                <li key={`help-list-${index}-${itemIndex}`} className="leading-snug">
                  {item}
                </li>
              ))}
            </ul>
          )
        }

        return (
          <p key={`help-paragraph-${index}`} className="leading-snug">
            {segment.content}
          </p>
        )
      })}
    </div>
  )
}

export function ProviderConfigForm({
  provider,
  onSuccess,
  additionalButtons,
}: ProviderConfigFormProps) {
  const {
    metadata: { id },
    scopes: { default: defaultScopes },
    grant_type: grantType,
    default_authorization_endpoint: providerDefaultAuth,
    default_token_endpoint: providerDefaultToken,
    authorization_endpoint_help: providerAuthHelp,
    token_endpoint_help: providerTokenHelp,
  } = provider
  const workspaceId = useWorkspaceId()
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
  const integrationAuthEndpoint = (
    integration as unknown as { authorization_endpoint?: string } | null
  )?.authorization_endpoint
  const integrationTokenEndpoint = (
    integration as unknown as { token_endpoint?: string } | null
  )?.token_endpoint

  const oauthSchema = z.object({
    authorization_endpoint: z
      .string()
      .min(1, { message: "Authorization endpoint is required" })
      .url({ message: "Enter a valid HTTPS URL" }),
    token_endpoint: z
      .string()
      .min(1, { message: "Token endpoint is required" })
      .url({ message: "Enter a valid HTTPS URL" }),
    client_id: z.string().max(512).nullish(),
    client_secret: z.string().max(512).optional(),
    scopes: z.array(z.string().min(1)).optional(),
  })
  type OAuthSchema = z.infer<typeof oauthSchema>

  const defaultValues = useMemo<OAuthSchema>(
    () => ({
      authorization_endpoint:
        integrationAuthEndpoint ?? providerDefaultAuth ?? "",
      token_endpoint: integrationTokenEndpoint ?? providerDefaultToken ?? "",
      client_id: integration?.client_id ?? undefined,
      client_secret: "", // Never pre-fill secrets
      scopes: integration?.requested_scopes ?? defaultScopes ?? [],
    }),
    [
      integration,
      integrationAuthEndpoint,
      integrationTokenEndpoint,
      defaultScopes,
      providerDefaultAuth,
      providerDefaultToken,
    ]
  )

  const form = useForm<OAuthSchema>({
    resolver: zodResolver(oauthSchema),
    defaultValues,
  })

  const onSubmit = useCallback(
    async (data: OAuthSchema) => {
      const {
        authorization_endpoint,
        token_endpoint,
        client_id,
        client_secret,
        scopes,
      } = data

      try {
        const trimmedClientId = client_id?.toString().trim()
        const trimmedClientSecret = client_secret?.toString().trim()
        const trimmedAuthEndpoint = authorization_endpoint.trim()
        const trimmedTokenEndpoint = token_endpoint.trim()

        const params: IntegrationUpdatePayload = {
          authorization_endpoint: trimmedAuthEndpoint,
          token_endpoint: trimmedTokenEndpoint,
          client_id: trimmedClientId ? trimmedClientId : undefined,
          client_secret: trimmedClientSecret ? trimmedClientSecret : undefined,
          scopes: scopes && scopes.length > 0 ? scopes : undefined,
          grant_type: grantType,
        }
        await updateIntegration(params)
        onSuccess?.()
      } catch (error) {
        console.error(error)
      }
    },
    [updateIntegration, onSuccess, grantType]
  )

  if (integrationIsLoading) {
    return <ProviderConfigFormSkeleton />
  }

  return (
    <div className="flex flex-col gap-6">
      {/* Current Configuration Summary */}
      {integration && (
        <Card className="bg-muted/50">
          <CardHeader>
            <div className="flex flex-col gap-1">
              <CardTitle>Current configuration</CardTitle>
              <CardDescription>
                View your existing integration settings.
              </CardDescription>
            </div>
          </CardHeader>
          <CardContent>
            <div className="grid grid-cols-1 md:grid-cols-2 gap-5 text-sm">
              <div className="flex flex-col gap-2">
                <span className="font-medium text-muted-foreground">
                  Authorization endpoint
                </span>
                <div className="text-xs font-mono break-all">
                  {integrationAuthEndpoint ??
                    providerDefaultAuth ??
                    "Using provider default"}
                </div>
              </div>
              <div className="flex flex-col gap-2">
                <span className="font-medium text-muted-foreground">
                  Token endpoint
                </span>
                <div className="text-xs font-mono break-all">
                  {integrationTokenEndpoint ??
                    providerDefaultToken ??
                    "Using provider default"}
                </div>
              </div>
              <div className="flex flex-col gap-2">
                <span className="font-medium text-muted-foreground">
                  Client ID
                </span>
                <div className="text-xs font-mono">
                  {integration.client_id ? (
                    <span>
                      {integration.client_id.slice(0, 8)}****
                      {integration.client_id.length > 12
                        ? "****" + integration.client_id.slice(-4)
                        : ""}
                    </span>
                  ) : (
                    <span className="text-muted-foreground">
                      Not configured
                    </span>
                  )}
                </div>
              </div>
              <div className="flex flex-col gap-2">
                <span className="font-medium text-muted-foreground">
                  Client secret
                </span>
                <div className="text-xs font-mono">
                  {integration.status !== "not_configured" ? (
                    <span>******</span>
                  ) : (
                    <span className="text-muted-foreground">
                      Not configured
                    </span>
                  )}
                </div>
              </div>
              <div className="flex flex-col gap-2 md:col-span-2">
                <span className="font-medium text-muted-foreground">
                  OAuth scopes
                </span>
                <div className="text-xs">
                  {(integration.requested_scopes ?? []).length > 0 ? (
                    <div className="flex flex-wrap gap-1">
                      {integration.requested_scopes?.map((scope) => (
                        <Badge key={scope} variant="outline" className="text-xs">
                          {scope}
                        </Badge>
                      ))}
                    </div>
                  ) : (
                    <span className="text-muted-foreground">
                      None configured
                    </span>
                  )}
                </div>
              </div>
            </div>
          </CardContent>
        </Card>
      )}

      <Form {...form}>
        <form
          onSubmit={form.handleSubmit(onSubmit)}
          className="flex flex-col gap-6"
        >
          {/* Client Credentials and Provider Configuration Card */}
          <Card>
            <CardHeader>
              <CardTitle>Client credentials</CardTitle>
            </CardHeader>
            <CardContent className="flex flex-col gap-4">
              <FormField
                control={form.control}
                name="authorization_endpoint"
                render={({ field }) => (
                  <FormItem>
                    <FormLabel>Authorization endpoint</FormLabel>
                    <FormControl>
                      <Input
                        {...field}
                        type="url"
                        value={field.value ?? ""}
                        placeholder="https://login.microsoftonline.com/{tenant}/oauth2/v2.0/authorize"
                      />
                    </FormControl>
                    <FormMessage />
                    <FormDescription className="text-xs">
                      <ProviderHelpDescription
                        text={
                          providerAuthHelp ||
                          "Update if you use a custom domain or sovereign cloud."
                        }
                      />
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
                        type="url"
                        value={field.value ?? ""}
                        placeholder="https://login.microsoftonline.com/{tenant}/oauth2/v2.0/token"
                      />
                    </FormControl>
                    <FormMessage />
                    <FormDescription className="text-xs">
                      <ProviderHelpDescription
                        text={
                          providerTokenHelp ||
                          "Update if you use a custom domain or sovereign cloud."
                        }
                      />
                    </FormDescription>
                  </FormItem>
                )}
              />
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
                        placeholder="Enter client ID..."
                      />
                    </FormControl>
                    <FormMessage />
                    <FormDescription className="text-xs">
                      The client ID for the OAuth application.
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
                        placeholder="Enter client secret..."
                      />
                    </FormControl>
                    <FormMessage />
                    <FormDescription className="text-xs">
                      The client secret for the OAuth application. Leave empty
                      to keep the existing secret unchanged.
                    </FormDescription>
                  </FormItem>
                )}
              />
            </CardContent>
          </Card>

          {/* OAuth Scopes Card */}
          <Card>
            <CardHeader>
              <CardTitle>OAuth scopes</CardTitle>
            </CardHeader>
            <CardContent className="space-y-4">
              {/* Show default scopes */}
              {defaultScopes && defaultScopes.length > 0 && (
                <div className="space-y-2">
                  <Label className="text-sm font-medium flex items-center gap-2">
                    Default scopes
                    <TooltipProvider>
                      <Tooltip>
                        <TooltipTrigger asChild>
                          <Info className="h-3 w-3 text-muted-foreground" />
                        </TooltipTrigger>
                        <TooltipContent>
                          <p className="text-xs">
                            These are the default scopes for this provider. You
                            can override them below.
                          </p>
                        </TooltipContent>
                      </Tooltip>
                    </TooltipProvider>
                  </Label>
                  <div className="flex flex-wrap gap-2">
                    {defaultScopes.map((scope) => (
                      <Badge key={scope} variant="secondary">
                        {scope}
                      </Badge>
                    ))}
                  </div>
                </div>
              )}

              {/* Scopes input */}
              <FormField
                control={form.control}
                name="scopes"
                render={({ field, fieldState }) => (
                  <FormItem>
                    <div className="flex items-center justify-between">
                      <FormLabel>OAuth scopes</FormLabel>
                      <Button
                        type="button"
                        variant="ghost"
                        size="sm"
                        onClick={() => field.onChange(defaultScopes ?? [])}
                        className="h-7 text-xs"
                      >
                        Reset scopes
                      </Button>
                    </div>
                    <FormControl>
                      <MultiTagCommandInput
                        value={field.value ?? []}
                        onChange={field.onChange}
                        placeholder="Add scopes..."
                        className="min-h-[42px]"
                        searchKeys={["value", "label", "description"]}
                        allowCustomTags
                        disableSuggestions
                      />
                    </FormControl>
                    <FormDescription className="text-xs">
                      <div className="flex flex-col gap-4">
                        <span>
                          Configure the OAuth scopes for this integration.
                        </span>
                      </div>
                    </FormDescription>
                    {fieldState.error && <FormMessage />}
                  </FormItem>
                )}
              />
            </CardContent>
          </Card>

          {/* Submit Button */}
          <div className="flex justify-end items-center pt-4 gap-2">
            <div className="flex flex-wrap gap-3">{additionalButtons}</div>
            <Button
              type="submit"
              variant={
                integration && integration.status !== "not_configured"
                  ? "secondary"
                  : "default"
              }
              disabled={updateIntegrationIsPending}
            >
              <Save className="h-4 w-4 mr-2" />
              {integration && integration.status !== "not_configured"
                ? "Update configuration"
                : "Save configuration"}
            </Button>
          </div>
        </form>
      </Form>
    </div>
  )
}

export function ProviderConfigFormSkeleton() {
  return (
    <div className="flex flex-col gap-6">
      <div className="rounded-md border p-4 bg-muted/50">
        <div className="h-4 bg-muted animate-pulse rounded mb-3"></div>
        <div className="grid grid-cols-1 md:grid-cols-2 gap-3">
          <div className="flex flex-col gap-2">
            <div className="h-3 bg-muted animate-pulse rounded w-20"></div>
            <div className="h-3 bg-muted animate-pulse rounded w-32"></div>
          </div>
          <div className="flex flex-col gap-2">
            <div className="h-3 bg-muted animate-pulse rounded w-24"></div>
            <div className="h-3 bg-muted animate-pulse rounded w-28"></div>
          </div>
        </div>
      </div>

      {/* Client Credentials Card Skeleton */}
      <Card>
        <CardHeader>
          <div className="h-5 bg-muted animate-pulse rounded w-40"></div>
        </CardHeader>
        <CardContent className="flex flex-col gap-4">
          <div className="flex flex-col gap-2">
            <div className="h-3 bg-muted animate-pulse rounded w-20"></div>
            <div className="h-10 bg-muted animate-pulse rounded"></div>
          </div>
          <div className="flex flex-col gap-2">
            <div className="h-3 bg-muted animate-pulse rounded w-24"></div>
            <div className="h-10 bg-muted animate-pulse rounded"></div>
          </div>
        </CardContent>
      </Card>

      {/* OAuth Scopes Card Skeleton */}
      <Card>
        <CardHeader>
          <div className="h-5 bg-muted animate-pulse rounded w-32"></div>
        </CardHeader>
        <CardContent className="flex flex-col gap-4">
          <div className="flex flex-col gap-2">
            <div className="h-3 bg-muted animate-pulse rounded w-24"></div>
            <div className="flex gap-2">
              <div className="h-6 bg-muted animate-pulse rounded w-16"></div>
              <div className="h-6 bg-muted animate-pulse rounded w-20"></div>
              <div className="h-6 bg-muted animate-pulse rounded w-20"></div>
            </div>
          </div>
          <div className="flex flex-col gap-2">
            <div className="h-3 bg-muted animate-pulse rounded w-40"></div>
            <div className="h-10 bg-muted animate-pulse rounded"></div>
          </div>
        </CardContent>
      </Card>
    </div>
  )
}
