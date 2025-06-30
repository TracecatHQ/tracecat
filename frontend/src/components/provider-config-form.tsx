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
  const { updateIntegration, updateIntegrationIsPending } =
    useIntegrationProvider({
      providerId: id,
      workspaceId,
    })
  const properties = Object.entries(schema.properties || {})
  const zodSchema = useMemo(() => jsonSchemaToZod(schema), [schema])

  const oauthSchema = z.object({
    client_id: z.string().min(1).max(512),
    client_secret: z.string().min(1).max(512),
    scopes: z
      .array(z.string())
      .optional()
      .refine(
        (scopes) => {
          if (!scopes || scopes.length === 0) return true
          if (!allowedPatterns || allowedPatterns.length === 0) return true

          return scopes.every((scope) =>
            allowedPatterns.some((pattern) => {
              try {
                const regex = new RegExp(pattern)
                return regex.test(scope)
              } catch {
                // If regex is invalid, skip this pattern
                return false
              }
            })
          )
        },
        {
          message:
            "One or more scopes don't match the allowed patterns for this provider",
        }
      ),
    config: zodSchema,
  })
  type OAuthSchema = z.infer<typeof oauthSchema>

  const defaultValues = useMemo(() => {
    return Object.fromEntries(
      properties.map(([key, property]) => [
        key,
        getDefaultValue(property as JSONSchema7),
      ])
    )
  }, [properties])

  const form = useForm<OAuthSchema>({
    resolver: zodResolver(oauthSchema),
    defaultValues,
  })

  const onSubmit = useCallback(
    async (data: OAuthSchema) => {
      const { client_id, client_secret, scopes, config } = data

      try {
        const params: IntegrationUpdate = {
          client_id: String(client_id),
          client_secret: String(client_secret),
          provider_config: config || {}, // If no config is provided, set an empty object
          scopes: scopes || undefined, // Only send if not empty
        }
        console.log(params)
        await updateIntegration(params)
        onSuccess?.()
      } catch (error) {
        console.error(error)
      }
    },
    [updateIntegration, onSuccess]
  )

  return (
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
                  <Input {...field} />
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
                  <Input {...field} type="password" />
                </FormControl>
                <FormMessage />
                <FormDescription>
                  The client secret for the OAuth application.
                </FormDescription>
              </FormItem>
            )}
          />

          {/* OAuth Scopes Configuration */}
          <div className="space-y-4">
            {/* Show default scopes */}
            {defaultScopes && defaultScopes.length > 0 && (
              <div>
                <p className="text-sm font-medium text-muted-foreground mb-2">
                  Default Scopes
                </p>
                <div className="flex flex-wrap gap-1">
                  {defaultScopes.map((scope) => (
                    <Badge key={scope} variant="secondary" className="text-xs">
                      {scope}
                    </Badge>
                  ))}
                </div>
                <p className="text-xs text-muted-foreground mt-1">
                  These scopes will be used if no custom scopes are specified.
                </p>
              </div>
            )}

            <FormField
              control={form.control}
              name="scopes"
              render={({ field }) => (
                <FormItem>
                  <FormLabel>Custom OAuth Scopes (Optional)</FormLabel>
                  <FormControl>
                    <MultiTagCommandInput
                      value={field.value || []}
                      onChange={field.onChange}
                      placeholder="Add custom scopes..."
                      searchKeys={["value", "label", "description"]}
                      className="min-h-[42px]"
                    />
                  </FormControl>
                  <FormDescription>
                    Override default scopes with custom ones. Leave empty to use
                    defaults. Start typing to see suggestions or enter any valid
                    scope.
                    {allowedPatterns && allowedPatterns.length > 0 && (
                      <div className="mt-2 p-2 bg-muted rounded text-xs">
                        <strong>Allowed scope patterns:</strong>
                        <ul className="mt-1 space-y-1">
                          {allowedPatterns.map((pattern, index) => (
                            <li key={index} className="font-mono text-xs">
                              {pattern}
                            </li>
                          ))}
                        </ul>
                      </div>
                    )}
                  </FormDescription>
                  <FormMessage />
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
  )
}

export function ProviderConfigFormSkeleton() {
  return <div>Loading...</div>
}
