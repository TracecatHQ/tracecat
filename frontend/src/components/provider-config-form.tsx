"use client"

import { zodResolver } from "@hookform/resolvers/zod"
import type { JSONSchema7 } from "json-schema"
import { Info, Save } from "lucide-react"
import { type HTMLInputTypeAttribute, useCallback, useMemo } from "react"
import { useForm } from "react-hook-form"
import { z } from "zod"
import type { IntegrationUpdate, ProviderRead } from "@/client"
import { MultiTagCommandInput } from "@/components/tags-input"
import { Badge } from "@/components/ui/badge"
import { Button } from "@/components/ui/button"
import { Card, CardContent, CardHeader, CardTitle } from "@/components/ui/card"
import { CollapsibleCard } from "@/components/ui/collapsible-card"
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
  Select,
  SelectContent,
  SelectItem,
  SelectTrigger,
  SelectValue,
} from "@/components/ui/select"
import { Switch } from "@/components/ui/switch"
import { Textarea } from "@/components/ui/textarea"
import {
  Tooltip,
  TooltipContent,
  TooltipProvider,
  TooltipTrigger,
} from "@/components/ui/tooltip"
import { useIntegrationProvider } from "@/lib/hooks"
import { jsonSchemaToZod } from "@/lib/jsonschema"
import { isMCPProvider } from "@/lib/providers"
import { useWorkspaceId } from "@/providers/workspace-id"

function getInputType(schemaProperty: JSONSchema7): HTMLInputTypeAttribute {
  switch (schemaProperty.type) {
    case "string":
      if (schemaProperty.format === "email") return "email"
      if (schemaProperty.format === "uri") return "url"
      if (schemaProperty.format === "password") return "password"
      return "text"
    case "number":
    case "integer":
      return "number"
    default:
      return "text"
  }
}

function getDefaultValue(schemaProperty: JSONSchema7): unknown {
  if (schemaProperty.default !== undefined) {
    return schemaProperty.default
  }
  switch (schemaProperty.type) {
    case "string":
      return ""
    case "number":
    case "integer":
      return 0
    case "boolean":
      return false
    case "array":
      return []
    case "object":
      return {}
    default:
      return ""
  }
}

const TEXT_AREA_THRESHOLD = 512

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
  const schema = provider.config_schema?.json_schema || {}
  const {
    metadata: { id },
    scopes: { default: defaultScopes },
    grant_type: grantType,
  } = provider
  const workspaceId = useWorkspaceId()
  const _isMCP = isMCPProvider(provider)
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
  const properties = Object.entries(schema.properties || {})
  const zodSchema = useMemo(() => jsonSchemaToZod(schema), [schema])

  const oauthSchema = z.object({
    client_id: z.string().min(1).max(512).nullish(),
    client_secret: z.string().max(512).optional(),
    scopes: z
      .array(z.string().min(1))
      .min(1, { message: "At least one scope is required" }),
    config: zodSchema,
  })
  type OAuthSchema = z.infer<typeof oauthSchema>

  const defaultValues = useMemo<OAuthSchema>(() => {
    const configDefaults = Object.fromEntries(
      properties.map(([key, property]) => [
        key,
        getDefaultValue(property as JSONSchema7),
      ])
    )

    return {
      client_id: integration?.client_id ?? "",
      client_secret: "", // Never pre-fill secrets
      scopes: integration?.requested_scopes ?? defaultScopes ?? [],
      config: {
        ...configDefaults,
        ...(integration?.provider_config ?? {}),
      },
    }
  }, [integration, properties, defaultScopes])

  const form = useForm<OAuthSchema>({
    resolver: zodResolver(oauthSchema),
    defaultValues,
  })

  const onSubmit = useCallback(
    async (data: OAuthSchema) => {
      const { client_id, client_secret, scopes, config } = data

      try {
        const params: IntegrationUpdate = {
          client_id: client_id ? String(client_id) : undefined,
          client_secret:
            client_secret && client_secret.trim()
              ? String(client_secret)
              : undefined,
          provider_config: config || undefined,
          scopes: scopes || undefined,
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
        <CollapsibleCard
          title="Current configuration"
          description="View your existing integration settings."
          defaultOpen={false}
          className="bg-muted/50"
        >
          <div className="grid grid-cols-1 md:grid-cols-2 gap-5 text-sm">
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
                  <span className="text-muted-foreground">Not configured</span>
                )}
              </div>
            </div>

            <div className="flex flex-col gap-2">
              <span className="font-medium text-muted-foreground">
                Client secret
              </span>
              <div className="text-xs">
                {integration.status !== "not_configured"
                  ? "Configured"
                  : "Not configured"}
              </div>
            </div>

            {integration.provider_config &&
              Object.keys(integration.provider_config).length > 0 && (
                <div className="flex flex-col gap-2 md:col-span-2">
                  <span className="font-medium text-muted-foreground">
                    Provider configuration
                  </span>
                  <div className="flex flex-col gap-2">
                    {Object.entries(integration.provider_config).map(
                      ([key, value]) => (
                        <div
                          key={key}
                          className="flex items-start gap-3 text-xs"
                        >
                          <span className="font-medium text-foreground min-w-0 flex-shrink-0">
                            {key
                              .replace(/_/g, " ")
                              .replace(/\b\w/g, (l) => l.toUpperCase())}
                          </span>
                          <span className="font-mono text-muted-foreground min-w-0 flex-1 truncate">
                            {typeof value === "string" && value.length > 32
                              ? `${value.slice(0, 24)}...`
                              : String(value)}
                          </span>
                        </div>
                      )
                    )}
                  </div>
                </div>
              )}

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
                  <span className="text-muted-foreground">None configured</span>
                )}
              </div>
            </div>
          </div>
        </CollapsibleCard>
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

              {/* Provider-Specific Configuration within the same card */}
              {properties.length > 0 && (
                <div className="flex flex-col gap-4">
                  {properties.map(([key, property]) => {
                    if (typeof property === "boolean") {
                      return null
                    }
                    const keyName = `config.${key}` as const
                    const enumOptions = property.enum
                    if (enumOptions) {
                      return (
                        <FormField
                          key={key}
                          control={form.control}
                          name={keyName}
                          render={({ field }) => (
                            <FormItem>
                              <FormLabel>
                                {property.title ||
                                  key
                                    .replace(/_/g, " ")
                                    .replace(/\b\w/g, (l) => l.toUpperCase())}
                                {property.required && (
                                  <span className="ml-1 text-red-500">*</span>
                                )}
                              </FormLabel>
                              <FormControl>
                                <Select
                                  onValueChange={field.onChange}
                                  defaultValue={
                                    field.value ? String(field.value) : ""
                                  }
                                >
                                  <SelectTrigger>
                                    <SelectValue />
                                  </SelectTrigger>
                                  <SelectContent>
                                    {enumOptions.map((option: unknown) => (
                                      <SelectItem
                                        key={String(option)}
                                        value={String(option)}
                                      >
                                        {String(option)}
                                      </SelectItem>
                                    ))}
                                  </SelectContent>
                                </Select>
                              </FormControl>
                              {property.description && (
                                <FormDescription className="text-xs">
                                  {property.description}
                                </FormDescription>
                              )}
                              <FormMessage />
                            </FormItem>
                          )}
                        />
                      )
                    }

                    if (property.type === "boolean") {
                      return (
                        <FormField
                          key={key}
                          control={form.control}
                          name={keyName}
                          render={({ field }) => (
                            <FormItem>
                              <div className="flex items-center space-x-2">
                                <FormControl>
                                  <Switch
                                    id={key}
                                    checked={Boolean(field.value)}
                                    onCheckedChange={field.onChange}
                                  />
                                </FormControl>
                                <FormLabel htmlFor={key}>
                                  {property.title ||
                                    key
                                      .replace(/_/g, " ")
                                      .replace(/\b\w/g, (l) => l.toUpperCase())}
                                  {property.required && (
                                    <span className="ml-1 text-red-500">*</span>
                                  )}
                                </FormLabel>
                              </div>
                              {property.description && (
                                <FormDescription className="text-xs">
                                  {property.description}
                                </FormDescription>
                              )}
                              <FormMessage />
                            </FormItem>
                          )}
                        />
                      )
                    }

                    if (
                      property.type === "string" &&
                      (property.maxLength === undefined ||
                        property.maxLength > TEXT_AREA_THRESHOLD)
                    ) {
                      return (
                        <FormField
                          key={key}
                          control={form.control}
                          name={keyName}
                          render={({ field }) => (
                            <FormItem>
                              <FormLabel>
                                {property.title ||
                                  key
                                    .replace(/_/g, " ")
                                    .replace(/\b\w/g, (l) => l.toUpperCase())}
                                {property.required && (
                                  <span className="ml-1 text-red-500">*</span>
                                )}
                              </FormLabel>
                              <FormControl>
                                <Textarea
                                  {...field}
                                  value={field.value ? String(field.value) : ""}
                                  placeholder={`Enter ${property.title || key.replace(/_/g, " ").toLowerCase()}...`}
                                />
                              </FormControl>
                              {property.description && (
                                <FormDescription className="text-xs">
                                  {property.description}
                                </FormDescription>
                              )}
                              <FormMessage />
                            </FormItem>
                          )}
                        />
                      )
                    }

                    return (
                      <FormField
                        key={key}
                        control={form.control}
                        name={keyName}
                        render={({ field }) => (
                          <FormItem>
                            <FormLabel>
                              {property.title ||
                                key
                                  .replace(/_/g, " ")
                                  .replace(/\b\w/g, (l) => l.toUpperCase())}
                              {property.required && (
                                <span className="ml-1 text-red-500">*</span>
                              )}
                            </FormLabel>
                            <FormControl>
                              <Input
                                {...field}
                                value={field.value ? String(field.value) : ""}
                                type={getInputType(property)}
                                placeholder={`Enter ${property.title || key.replace(/_/g, " ").toLowerCase()}...`}
                              />
                            </FormControl>
                            {property.description && (
                              <FormDescription className="text-xs">
                                {property.description}
                              </FormDescription>
                            )}
                            <FormMessage />
                          </FormItem>
                        )}
                      />
                    )
                  })}
                </div>
              )}
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
