"use client"

import { zodResolver } from "@hookform/resolvers/zod"
import { KeyIcon } from "lucide-react"
import { useEffect, useState } from "react"
import { type ControllerRenderProps, useForm } from "react-hook-form"
import { z } from "zod"
import {
  agentCreateProviderCredentials,
  agentUpdateProviderCredentials,
  type ProviderCredentialField,
} from "@/client"
import { Spinner } from "@/components/loading/spinner"
import { ServiceAccountJsonUploader } from "@/components/service-account-json-uploader"
import { Button } from "@/components/ui/button"
import {
  Dialog,
  DialogBody,
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
import { Switch } from "@/components/ui/switch"
import {
  getApiErrorCode,
  getApiErrorDetail,
  type TracecatApiError,
} from "@/lib/errors"
import { useProviderCredentialConfig } from "@/lib/hooks"
import { cn } from "@/lib/utils"

interface AgentCredentialsDialogProps {
  provider: string | null
  providerConfigured: boolean
  isOpen: boolean
  onClose: () => void
  onSuccess: () => Promise<void> | void
}

interface CredentialLayoutSection {
  title?: string
  description?: string
  rows: string[][]
}

function isJsonCredentialField(fieldKey: string): boolean {
  return fieldKey === "GOOGLE_API_CREDENTIALS"
}

function getCredentialLayoutSections(
  provider: string
): CredentialLayoutSection[] {
  if (provider === "bedrock") {
    return [
      {
        title: "Authentication",
        description:
          "Use either AWS access keys or a Bedrock bearer token for this provider.",
        rows: [
          ["AWS_ROLE_ARN", "AWS_ROLE_SESSION_NAME"],
          ["AWS_ACCESS_KEY_ID", "AWS_SECRET_ACCESS_KEY"],
          ["AWS_BEARER_TOKEN_BEDROCK"],
        ],
      },
      {
        title: "Request defaults",
        description:
          "Set the required region and optional fallback targets for Bedrock routing.",
        rows: [["AWS_REGION"], ["AWS_MODEL_ID", "AWS_INFERENCE_PROFILE_ID"]],
      },
    ]
  }

  return []
}

function getCredentialFieldRows(
  providerFields: { key: string }[],
  layoutSections: CredentialLayoutSection[]
): CredentialLayoutSection[] {
  if (layoutSections.length === 0) {
    return [
      {
        rows: providerFields.map((field) => [field.key]),
      },
    ]
  }

  const placedFieldKeys = new Set(
    layoutSections.flatMap((section) => section.rows.flat())
  )
  const unplacedRows = providerFields
    .filter((field) => !placedFieldKeys.has(field.key))
    .map((field) => [field.key])

  if (unplacedRows.length === 0) {
    return layoutSections
  }
  return [
    ...layoutSections,
    {
      rows: unplacedRows,
    },
  ]
}

export function AgentCredentialsDialog({
  provider,
  providerConfigured,
  isOpen,
  onClose,
  onSuccess,
}: AgentCredentialsDialogProps) {
  const [loading, setLoading] = useState(false)

  const { providerConfig, providerConfigLoading, providerConfigError } =
    useProviderCredentialConfig(provider)

  const getCredentialsSchema = () => {
    if (!providerConfig?.fields) {
      return z.object({})
    }

    const schemaFields: Record<
      string,
      z.ZodString | z.ZodOptional<z.ZodString>
    > = {}
    for (const field of providerConfig.fields) {
      if (field.required === false) {
        schemaFields[field.key] = z.string().optional()
      } else {
        schemaFields[field.key] = z
          .string()
          .min(1, `${field.label} is required`)
      }
    }
    return z.object(schemaFields)
  }

  const form = useForm<Record<string, string>>({
    resolver:
      provider && providerConfig
        ? zodResolver(getCredentialsSchema())
        : undefined,
  })

  useEffect(() => {
    if (provider && providerConfig) {
      const defaults = Object.fromEntries(
        providerConfig.fields
          .filter((field) => field.default != null)
          .map((field) => [field.key, field.default ?? ""])
      )
      form.reset(defaults)
    }
  }, [provider, providerConfig, form])

  const onSubmit = async (data: Record<string, string>) => {
    if (!provider || !providerConfig) return

    try {
      setLoading(true)
      form.clearErrors("root")
      const normalizedCredentials = Object.fromEntries(
        providerConfig.fields.map((field) => {
          const rawValue = data[field.key]
          return [field.key, rawValue?.trim() || ""]
        })
      )

      if (providerConfigured) {
        await agentUpdateProviderCredentials({
          provider,
          requestBody: {
            credentials: normalizedCredentials,
          },
        })
      } else {
        await agentCreateProviderCredentials({
          requestBody: {
            provider,
            credentials: normalizedCredentials,
          },
        })
      }

      await onSuccess()
      onClose()
    } catch (err) {
      const message =
        getApiErrorDetail(err) ?? "Couldn't save credentials. Please try again."
      switch (getApiErrorCode(err)) {
        case "provider_key_invalid":
        case "provider_key_access_denied": {
          const { field } = (err as TracecatApiError<{ field: string }>).body
            .detail!
          form.setError(
            field,
            { type: "server", message },
            { shouldFocus: true }
          )
          break
        }
        default:
          form.setError("root", { type: "server", message })
      }
    } finally {
      setLoading(false)
    }
  }

  function renderCredentialInput(
    field: ProviderCredentialField,
    formField: ControllerRenderProps<Record<string, string>, string>
  ) {
    if (field.type === "boolean") {
      return (
        <div className="flex h-9 items-center">
          <Switch
            checked={formField.value === "true"}
            onCheckedChange={(checked) =>
              formField.onChange(checked ? "true" : "false")
            }
          />
        </div>
      )
    }
    if (isJsonCredentialField(field.key)) {
      return (
        <ServiceAccountJsonUploader
          value={formField.value ?? ""}
          onChange={formField.onChange}
          onError={(message) => {
            form.setError(field.key, {
              type: "manual",
              message,
            })
          }}
          onClearError={() => {
            form.clearErrors(field.key)
          }}
          placeholder="Drag & drop the JSON key (.json) or choose a file"
          existingConfigured={false}
          hasError={Boolean(form.formState.errors[field.key])}
        />
      )
    }
    return (
      <Input
        placeholder={`Enter your ${field.label.toLowerCase()}`}
        type={field.type}
        {...formField}
      />
    )
  }

  if (!provider) return null

  const fieldsByKey = new Map(
    (providerConfig?.fields ?? []).map((field) => [field.key, field])
  )
  const layoutSections = provider ? getCredentialLayoutSections(provider) : []
  const fieldRows = providerConfig
    ? getCredentialFieldRows(providerConfig.fields, layoutSections)
    : []
  const dialogLabel = providerConfig?.label ?? provider

  return (
    <Dialog open={isOpen} onOpenChange={onClose}>
      <DialogContent size="md" className="sm:max-w-[425px]">
        <DialogHeader>
          <DialogTitle className="flex items-center space-x-2">
            <KeyIcon className="size-5" />
            <span>Configure {dialogLabel} credentials</span>
          </DialogTitle>
          <DialogDescription>
            Enter your {dialogLabel} credentials to enable AI model access for
            your organization.
          </DialogDescription>
        </DialogHeader>

        {providerConfigLoading ? (
          <div className="flex items-center justify-center py-8">
            <Spinner className="size-6" />
          </div>
        ) : providerConfigError || !providerConfig ? (
          <p className="text-sm text-destructive">
            {providerConfigError
              ? "Couldn't load credential settings. Please refresh and try again."
              : `Credentials configuration for ${provider} is not available.`}
          </p>
        ) : (
          <Form {...form}>
            <form
              onSubmit={form.handleSubmit(onSubmit)}
              className="flex min-h-0 flex-1 flex-col"
            >
              <DialogBody>
                {fieldRows.map((section, sectionIndex) => (
                  <div
                    className="space-y-4"
                    key={section.title ?? `section-${sectionIndex}`}
                  >
                    {section.title ? (
                      <div className="space-y-1">
                        <p className="text-sm font-medium">{section.title}</p>
                        {section.description ? (
                          <p className="text-xs text-muted-foreground">
                            {section.description}
                          </p>
                        ) : null}
                      </div>
                    ) : null}
                    {section.rows.map((row, rowIndex) => {
                      const rowFields = row
                        .map((fieldKey) => fieldsByKey.get(fieldKey))
                        .filter((field) => field !== undefined)

                      if (!rowFields.length) {
                        return null
                      }

                      return (
                        <div
                          className={cn(
                            "grid gap-4",
                            rowFields.length > 1 && "sm:grid-cols-2"
                          )}
                          key={`${sectionIndex}-${rowIndex}`}
                        >
                          {rowFields.map((field) => (
                            <FormField
                              key={field.key}
                              control={form.control}
                              name={field.key}
                              render={({ field: formField }) => (
                                <FormItem>
                                  <FormLabel>
                                    {field.label}
                                    {field.required !== false && (
                                      <span className="ml-1 text-red-500">
                                        *
                                      </span>
                                    )}
                                  </FormLabel>
                                  <FormControl>
                                    {renderCredentialInput(field, formField)}
                                  </FormControl>
                                  <FormDescription>
                                    {field.description}
                                  </FormDescription>
                                  <FormMessage />
                                </FormItem>
                              )}
                            />
                          ))}
                        </div>
                      )
                    })}
                  </div>
                ))}

                {form.formState.errors.root && (
                  <p className="text-sm text-destructive">
                    {form.formState.errors.root.message}
                  </p>
                )}
              </DialogBody>
              <DialogFooter>
                <Button size="sm" type="submit" disabled={loading}>
                  {loading && <Spinner className="mr-2 size-4" />}
                  {loading ? "Saving…" : "Save credentials"}
                </Button>
              </DialogFooter>
            </form>
          </Form>
        )}
      </DialogContent>
    </Dialog>
  )
}
