"use client"

import { zodResolver } from "@hookform/resolvers/zod"
import type { JSONSchema7 } from "json-schema"
import { type HTMLInputTypeAttribute, useCallback, useMemo } from "react"
import { useForm } from "react-hook-form"
import { z } from "zod"
import type { IntegrationUpdate, ProviderRead } from "@/client"
import { MultiTagCommandInput } from "@/components/tags-input"
import { Badge } from "@/components/ui/badge"
import { Button } from "@/components/ui/button"
import { Checkbox } from "@/components/ui/checkbox"

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
import {
  Select,
  SelectContent,
  SelectItem,
  SelectTrigger,
  SelectValue,
} from "@/components/ui/select"
import { Textarea } from "@/components/ui/textarea"
import { useIntegrationProvider } from "@/lib/hooks"
import { jsonSchemaToZod } from "@/lib/jsonschema"
import { useWorkspace } from "@/providers/workspace"

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
}

export function ProviderConfigForm({
  provider,
  onSuccess,
}: ProviderConfigFormProps) {
  const schema = provider.schema?.json_schema || {}
  const {
    metadata: { id },
    scopes: { default: defaultScopes, allowed_patterns: allowedPatterns },
  } = provider
  const { workspaceId } = useWorkspace()
  const {
    integration,
    integrationIsLoading,
    updateIntegration,
    updateIntegrationIsPending,
  } = useIntegrationProvider({
    providerId: id,
    workspaceId,
  })
  const properties = Object.entries(schema.properties || {})
  const zodSchema = useMemo(() => jsonSchemaToZod(schema), [schema])

  const oauthSchema = z.object({
    client_id: z.string().min(1).max(512).nullish(),
    client_secret: z.string().max(512).optional(),
    additional_scopes: z
      .array(z.string())
      .optional()
      .superRefine((scopes, ctx) => {
        if (!scopes || scopes.length === 0) return
        if (!allowedPatterns || allowedPatterns.length === 0) return

        scopes.forEach((scope, scopeIndex) => {
          const failedPatterns: string[] = []

          allowedPatterns.forEach((pattern) => {
            try {
              const regex = new RegExp(pattern)
              if (!regex.test(scope)) {
                failedPatterns.push(pattern)
              }
            } catch {
              // If regex is invalid, skip this pattern
            }
          })

          // If scope failed any patterns, add an issue
          if (failedPatterns.length > 0) {
            ctx.addIssue({
              code: z.ZodIssueCode.custom,
              message: `Scope "${scope}" doesn't match required patterns: ${failedPatterns.join(", ")}`,
              path: [scopeIndex],
            })
          }
        })
      }),
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
      client_id: integration?.client_id ?? "", // Integration doesn't expose client_id
      client_secret: "", // Never pre-fill secrets
      // Show only additional scopes
      additional_scopes:
        integration?.requested_scopes?.filter(
          (scope) => !defaultScopes?.includes(scope)
        ) ?? [],
      config: {
        ...configDefaults,
        ...(integration?.provider_config ?? {}),
      },
    }
  }, [integration, properties])

  const form = useForm<OAuthSchema>({
    resolver: zodResolver(oauthSchema),
    defaultValues,
  })

  const onSubmit = useCallback(
    async (data: OAuthSchema) => {
      const { client_id, client_secret, additional_scopes, config } = data

      const scopes = [...(defaultScopes ?? []), ...(additional_scopes ?? [])]
      try {
        const params: IntegrationUpdate = {
          client_id: client_id ? String(client_id) : undefined,
          client_secret:
            client_secret && client_secret.trim()
              ? String(client_secret)
              : undefined,
          provider_config: config || undefined,
          // All scopes
          scopes: scopes || undefined,
        }
        await updateIntegration(params)
        onSuccess?.()
      } catch (error) {
        console.error(error)
      }
    },
    [updateIntegration, onSuccess, defaultScopes]
  )

  if (integrationIsLoading) {
    return <ProviderConfigFormSkeleton />
  }

  return (
    <div className="space-y-6">
      {/* Current Configuration Summary */}
      {integration && (
        <div className="rounded-md p-4 bg-muted/50 shadow-sm">
          <h4 className="text-sm font-medium mb-3">Current configuration</h4>
          <div className="grid grid-cols-1 md:grid-cols-2 gap-3 text-sm">
            <div className="space-y-1">
              <span className="font-medium text-muted-foreground">
                Client ID
              </span>
              <div className="text-xs">
                {integration.status === "connected"
                  ? "Configured"
                  : "Not configured"}
              </div>
            </div>

            <div className="space-y-1">
              <span className="font-medium text-muted-foreground">
                Client Secret
              </span>
              <div className="text-xs">
                {integration.status === "connected"
                  ? "Configured"
                  : "Not configured"}
              </div>
            </div>

            <div className="space-y-1 md:col-span-2">
              <span className="font-medium text-muted-foreground">
                OAuth Scopes
              </span>
              <div className="flex flex-wrap gap-1">
                {(integration.requested_scopes ?? []).length > 0 ? (
                  integration.requested_scopes?.map((scope) => (
                    <Badge key={scope} variant="outline" className="text-xs">
                      {scope}
                    </Badge>
                  ))
                ) : (
                  <span className="text-xs text-muted-foreground">
                    None configured
                  </span>
                )}
              </div>
            </div>

            {integration.provider_config &&
              Object.keys(integration.provider_config).length > 0 && (
                <div className="space-y-1 md:col-span-2">
                  <span className="font-medium text-muted-foreground">
                    Provider Configuration
                  </span>
                  <div className="text-xs space-y-1">
                    {Object.entries(integration.provider_config).map(
                      ([key, value]) => (
                        <div key={key} className="flex justify-between">
                          <span className="font-medium">{key}:</span>
                          <span className="font-mono">
                            {typeof value === "string" && value.length > 20
                              ? `${value.slice(0, 8)}...`
                              : String(value)}
                          </span>
                        </div>
                      )
                    )}
                  </div>
                </div>
              )}
          </div>
        </div>
      )}

      <Form {...form}>
        <form onSubmit={form.handleSubmit(onSubmit)} className="space-y-6">
          {/* Standard OAuth Configuration */}
          <div className="space-y-4">
            <h3 className="text-sm font-medium">OAuth Configuration</h3>
            <FormField
              control={form.control}
              name="client_id"
              render={({ field }) => (
                <FormItem>
                  <FormLabel>Client ID</FormLabel>
                  <FormControl>
                    <Input {...field} value={field.value ?? ""} />
                  </FormControl>
                  <FormMessage />
                  <FormDescription>
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
                  <FormLabel>Client Secret</FormLabel>
                  <FormControl>
                    <Input
                      {...field}
                      type="password"
                      value={field.value ?? ""}
                      placeholder="Enter new client secret..."
                    />
                  </FormControl>
                  <FormMessage />
                  <FormDescription>
                    The client secret for the OAuth application. Leave empty to
                    keep the existing secret unchanged.
                  </FormDescription>
                </FormItem>
              )}
            />

            {/* OAuth Scopes Configuration */}
            <div className="space-y-4">
              {/* Show default scopes */}
              {defaultScopes && defaultScopes.length > 0 && (
                <div>
                  <span className="text-sm font-medium text-muted-foreground mb-2">
                    Base Scopes
                  </span>
                  <div className="flex flex-wrap gap-1">
                    {defaultScopes.map((scope) => (
                      <Badge
                        key={scope}
                        variant="secondary"
                        className="text-xs"
                      >
                        {scope}
                      </Badge>
                    ))}
                  </div>
                  <span className="text-xs text-muted-foreground mt-1">
                    These are the base scopes that this integration requires.
                    You can customize additional scopes below.
                  </span>
                </div>
              )}

              <FormField
                control={form.control}
                name="additional_scopes"
                render={({ field, fieldState }) => (
                  <FormItem>
                    <FormLabel>Additional OAuth Scopes (Optional)</FormLabel>
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
                    <FormDescription>
                      Add additional scopes beyond the base scopes. Leave empty
                      to use only the base scopes. Start typing to see
                      suggestions or enter any valid scope.
                      {allowedPatterns && allowedPatterns.length > 0 && (
                        <div className="mt-2 p-2 bg-muted rounded text-xs">
                          <strong>Allowed scope patterns:</strong>
                          <div className="mt-1 space-y-1">
                            {allowedPatterns.map((pattern, index) => (
                              <div key={index} className="font-mono text-xs">
                                {pattern}
                              </div>
                            ))}
                          </div>
                        </div>
                      )}
                    </FormDescription>
                    <ScopeErrors
                      errors={
                        fieldState.error as
                          | Record<string, { message?: string }>
                          | undefined
                      }
                      scopes={field.value ?? []}
                    />
                    {fieldState.error &&
                      !Object.keys(fieldState.error).some(
                        (key) => !isNaN(Number(key))
                      ) && <FormMessage />}
                  </FormItem>
                )}
              />
            </div>
          </div>
          {/* Provider-Specific Configuration Section */}
          {properties.length > 0 && (
            <div className="space-y-4">
              <h3 className="text-sm font-medium">
                Provider-specific Configuration
              </h3>
              <div className="space-y-4">
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
                                  <SelectValue
                                    placeholder={`Select ${property.title || key}`}
                                  />
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
                              <FormDescription>
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
                                <Checkbox
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
                              <FormDescription>
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
                                placeholder={
                                  property.description ||
                                  `Enter ${property.title || key}`
                                }
                                value={field.value ? String(field.value) : ""}
                              />
                            </FormControl>
                            {property.description && (
                              <FormDescription>
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
                              placeholder={
                                property.description ||
                                `Enter ${property.title || key}`
                              }
                            />
                          </FormControl>
                          {property.description && (
                            <FormDescription>
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
            </div>
          )}

          {/* Submit Button */}
          <div className="flex justify-end pt-4">
            <Button type="submit" disabled={updateIntegrationIsPending}>
              {updateIntegrationIsPending ? "Saving..." : "Save Configuration"}
            </Button>
          </div>
        </form>
      </Form>
    </div>
  )
}

export function ProviderConfigFormSkeleton() {
  return (
    <div className="space-y-6">
      <div className="rounded-md border p-4 bg-muted/50">
        <div className="h-4 bg-muted animate-pulse rounded mb-3"></div>
        <div className="grid grid-cols-1 md:grid-cols-2 gap-3">
          <div className="space-y-2">
            <div className="h-3 bg-muted animate-pulse rounded w-20"></div>
            <div className="h-3 bg-muted animate-pulse rounded w-32"></div>
          </div>
          <div className="space-y-2">
            <div className="h-3 bg-muted animate-pulse rounded w-24"></div>
            <div className="h-3 bg-muted animate-pulse rounded w-28"></div>
          </div>
        </div>
      </div>
      <div className="space-y-4">
        <div className="h-4 bg-muted animate-pulse rounded w-32"></div>
        <div className="space-y-3">
          <div className="space-y-2">
            <div className="h-3 bg-muted animate-pulse rounded w-20"></div>
            <div className="h-10 bg-muted animate-pulse rounded"></div>
          </div>
          <div className="space-y-2">
            <div className="h-3 bg-muted animate-pulse rounded w-24"></div>
            <div className="h-10 bg-muted animate-pulse rounded"></div>
          </div>
        </div>
      </div>
    </div>
  )
}

function ScopeErrors({
  errors,
  scopes,
}: {
  errors: Record<string, { message?: string }> | undefined
  scopes: string[]
}) {
  if (!errors || !scopes) return null

  // Get all scope-specific errors (errors with numeric keys)
  const scopeErrors = Object.entries(errors)
    .filter(([key]) => !isNaN(Number(key)))
    .map(([index, error]) => ({
      index: Number(index),
      scope: scopes[Number(index)],
      message: error?.message || "Invalid scope",
    }))

  if (scopeErrors.length === 0) return null

  return (
    <div className="space-y-1">
      {scopeErrors.map(({ index, message }) => (
        <div key={index} className="text-sm text-red-600">
          {message}
        </div>
      ))}
    </div>
  )
}
