"use client"

import { zodResolver } from "@hookform/resolvers/zod"
import { KeyIcon } from "lucide-react"
import { useEffect, useState } from "react"
import { useForm } from "react-hook-form"
import { z } from "zod"
import { CenteredSpinner } from "@/components/loading/spinner"
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

interface AgentCredentialsDialogProps {
  provider: string | null
  isOpen: boolean
  onClose: () => void
  onSuccess: () => void
}

function isJsonCredentialField(fieldKey: string): boolean {
  return fieldKey === "GOOGLE_API_CREDENTIALS"
}

export function AgentCredentialsDialog({
  provider,
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
    if (!provider) return

    try {
      setLoading(true)
      setError(null)

      const response = await fetch("/api/agent/credentials", {
        method: "POST",
        headers: {
          "Content-Type": "application/json",
        },
        body: JSON.stringify({
          provider,
          credentials: data,
        }),
      })

      if (!response.ok) {
        const errorData = await response.json()
        throw new Error(errorData.detail || "Failed to save credentials")
      }

      onSuccess()
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

  if (providerConfigLoading) {
    return <CenteredSpinner />
  }

  if (providerConfigError || !providerConfig) {
    return (
      <Dialog open={isOpen} onOpenChange={onClose}>
        <DialogContent>
          <DialogHeader>
            <DialogTitle>Configuration Error</DialogTitle>
            <DialogDescription>
              {providerConfigError
                ? `Failed to load credential configuration: ${providerConfigError.message}`
                : `Credentials configuration for ${provider} is not available.`}
            </DialogDescription>
          </DialogHeader>
          <DialogFooter>
            <Button onClick={onClose}>Close</Button>
          </DialogFooter>
        </DialogContent>
      </Dialog>
    )
  }

  return (
    <Dialog open={isOpen} onOpenChange={onClose}>
      <DialogContent className="sm:max-w-[425px]">
        <DialogHeader>
          <DialogTitle className="flex items-center space-x-2">
            <KeyIcon className="size-5" />
            <span>Configure {providerConfig.label} credentials</span>
          </DialogTitle>
          <DialogDescription>
            Enter your {provider} credentials to enable AI model access for your
            organization.
          </DialogDescription>
        </DialogHeader>

        <Form {...form}>
          <form onSubmit={form.handleSubmit(onSubmit)} className="space-y-4">
            {providerConfig.fields.map((field) => (
              <FormField
                key={field.key}
                control={form.control}
                name={field.key}
                render={({ field: formField }) => (
                  <FormItem>
                    <FormLabel>
                      {field.label}
                      {field.required !== false && (
                        <span className="text-red-500 ml-1">*</span>
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
                          hasError={Boolean(form.formState.errors[field.key])}
                        />
                      ) : (
                        <Input
                          type={field.type}
                          placeholder={`Enter your ${field.label.toLowerCase()}`}
                          {...formField}
                        />
                      )}
                    </FormControl>
                    <FormDescription>{field.description}</FormDescription>
                    <FormMessage />
                  </FormItem>
                )}
              />
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
      </DialogContent>
    </Dialog>
  )
}
