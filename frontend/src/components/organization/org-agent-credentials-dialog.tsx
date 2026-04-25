"use client"

import { zodResolver } from "@hookform/resolvers/zod"
import { KeyIcon } from "lucide-react"
import { useEffect, useState } from "react"
import { useForm } from "react-hook-form"
import { z } from "zod"
import {
  agentCreateProviderCredentials,
  agentUpdateProviderCredentials,
} from "@/client"
import { Spinner } from "@/components/loading/spinner"
import { ServiceAccountJsonUploader } from "@/components/service-account-json-uploader"
import { Button } from "@/components/ui/button"
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
  const [error, setError] = useState<string | null>(null)

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
      form.reset()
      setError(null)
    }
  }, [provider, providerConfig, form])

  const onSubmit = async (data: Record<string, string>) => {
    if (!provider || !providerConfig) return

    try {
      setLoading(true)
      setError(null)
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
      setError(
        err instanceof Error ? err.message : "Failed to save credentials"
      )
    } finally {
      setLoading(false)
    }
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
      <DialogContent className="max-h-[90vh] overflow-y-auto sm:max-w-[425px]">
        <DialogHeader>
          <DialogTitle className="flex items-center space-x-2">
            <KeyIcon className="size-5" />
            <span>Configure {dialogLabel} credentials</span>
          </DialogTitle>
          <DialogDescription>
            Enter your {provider} credentials to enable AI model access for your
            organization.
          </DialogDescription>
        </DialogHeader>

        {providerConfigLoading ? (
          <div className="flex items-center justify-center py-8">
            <Spinner className="size-6" />
          </div>
        ) : providerConfigError || !providerConfig ? (
          <>
            <div className="rounded-md bg-red-50 p-3">
              <p className="text-sm text-red-700">
                {providerConfigError
                  ? `Failed to load credential configuration: ${providerConfigError.message}`
                  : `Credentials configuration for ${provider} is not available.`}
              </p>
            </div>
            <DialogFooter>
              <Button onClick={onClose}>Close</Button>
            </DialogFooter>
          </>
        ) : (
          <Form {...form}>
            <form onSubmit={form.handleSubmit(onSubmit)} className="space-y-4">
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
                                    <span className="ml-1 text-red-500">*</span>
                                  )}
                                </FormLabel>
                                <FormControl>
                                  {isJsonCredentialField(field.key) ? (
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
                                      hasError={Boolean(
                                        form.formState.errors[field.key]
                                      )}
                                    />
                                  ) : (
                                    <Input
                                      placeholder={`Enter your ${field.label.toLowerCase()}`}
                                      type={field.type}
                                      {...formField}
                                    />
                                  )}
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

              {error && (
                <div className="rounded-md bg-red-50 p-3">
                  <p className="text-sm text-red-700">{error}</p>
                </div>
              )}

              <DialogFooter>
                <Button type="button" variant="outline" onClick={onClose}>
                  Cancel
                </Button>
                <Button type="submit" disabled={loading}>
                  {loading ? "Saving..." : "Save credentials"}
                </Button>
              </DialogFooter>
            </form>
          </Form>
        )}
      </DialogContent>
    </Dialog>
  )
}
