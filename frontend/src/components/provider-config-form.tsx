"use client"

import { zodResolver } from "@hookform/resolvers/zod"
import type { JSONSchema7 } from "json-schema"
import { type HTMLInputTypeAttribute, useCallback, useMemo } from "react"
import { useForm } from "react-hook-form"
import { z } from "zod"
import type { IntegrationUpdate, ProviderMetadata } from "@/client"
import { Button } from "@/components/ui/button"
import { Checkbox } from "@/components/ui/checkbox"
import {
  Dialog,
  DialogContent,
  DialogDescription,
  DialogFooter,
  DialogHeader,
  DialogTitle,
} from "@/components/ui/dialog"
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

interface ProviderConfigFormProps {
  provider: ProviderMetadata
  isOpen: boolean
  onClose: () => void
  onSuccess?: () => void
}

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

export function ProviderConfigForm({
  provider,
  isOpen,
  onClose,
  onSuccess,
}: ProviderConfigFormProps) {
  const { workspaceId } = useWorkspace()
  const { providerSchema, providerSchemaIsLoading, providerSchemaError } =
    useIntegrationProvider({
      providerId: provider.id,
      workspaceId: workspaceId,
    })
  if (providerSchemaIsLoading) {
    return <ProviderConfigFormSkeleton />
  }
  if (providerSchemaError) {
    return <div>Error: {providerSchemaError.message}</div>
  }
  const schema = providerSchema?.json_schema || {}

  return (
    <ProviderConfigFormContent
      schema={schema}
      provider={provider}
      isOpen={isOpen}
      onClose={onClose}
      onSuccess={onSuccess}
    />
  )
}

interface ProviderConfigFormContentProps {
  schema: JSONSchema7
  provider: ProviderMetadata
  isOpen: boolean
  onClose: () => void
  onSuccess?: () => void
}
export function ProviderConfigFormContent({
  schema,
  provider,
  isOpen,
  onClose,
  onSuccess,
}: ProviderConfigFormContentProps) {
  const { workspaceId } = useWorkspace()
  const { updateIntegration, updateIntegrationIsPending } =
    useIntegrationProvider({ providerId: provider.id, workspaceId })
  const properties = Object.entries(schema.properties || {})
  const zodSchema = useMemo(() => jsonSchemaToZod(schema), [schema])

  const oauthSchema = z.object({
    client_id: z.string().min(1).max(512),
    client_secret: z.string().min(1).max(512),
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
      const { client_id, client_secret, config } = data

      try {
        const params: IntegrationUpdate = {
          client_id: String(client_id),
          client_secret: String(client_secret),
          provider_config: config || {}, // If no config is provided, set an empty object
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
    <Dialog open={isOpen} onOpenChange={onClose}>
      <DialogContent className="max-w-md">
        <DialogHeader>
          <DialogTitle>Configure {provider.name}</DialogTitle>
          <DialogDescription>
            {provider.description ||
              `Configure ${provider.name} integration settings`}
          </DialogDescription>
        </DialogHeader>

        <Form {...form}>
          <div className="space-y-6">
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
            </div>
            {/* Provider-Specific Configuration Section */}
            {properties.length > 0 && (
              <div className="space-y-4">
                <h3 className="text-sm font-medium">Provider Configuration</h3>
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
          </div>
        </Form>

        <DialogFooter>
          <Button
            variant="outline"
            onClick={onClose}
            disabled={updateIntegrationIsPending}
          >
            Cancel
          </Button>
          <Button
            onClick={form.handleSubmit(onSubmit)}
            disabled={updateIntegrationIsPending}
          >
            {updateIntegrationIsPending ? "Saving..." : "Save Configuration"}
          </Button>
        </DialogFooter>
      </DialogContent>
    </Dialog>
  )
}

export function ProviderConfigFormSkeleton() {
  return <div>Loading...</div>
}
