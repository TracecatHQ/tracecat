"use client"

import { zodResolver } from "@hookform/resolvers/zod"
import { KeyIcon, Upload } from "lucide-react"
import { type ChangeEvent, useEffect, useRef, useState } from "react"
import { type ControllerRenderProps, useForm } from "react-hook-form"
import { z } from "zod"
import { CenteredSpinner } from "@/components/loading/spinner"
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
import { Textarea } from "@/components/ui/textarea"
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

interface JsonCredentialInputProps {
  field: ControllerRenderProps<Record<string, string>, string>
}

function JsonCredentialInput({ field }: JsonCredentialInputProps) {
  const fileInputRef = useRef<HTMLInputElement | null>(null)

  const onUpload = (event: ChangeEvent<HTMLInputElement>) => {
    const file = event.target.files?.[0]
    if (!file) {
      return
    }
    const reader = new FileReader()
    reader.onload = () => {
      const text = typeof reader.result === "string" ? reader.result : ""
      field.onChange(text)
    }
    reader.readAsText(file)
    event.target.value = ""
  }

  return (
    <div className="space-y-2">
      <Textarea
        value={field.value ?? ""}
        onChange={field.onChange}
        placeholder="Paste service account JSON"
        className="min-h-36 font-mono text-xs"
      />
      <input
        ref={fileInputRef}
        type="file"
        accept=".json,application/json"
        className="hidden"
        onChange={onUpload}
      />
      <Button
        type="button"
        variant="outline"
        className="w-full"
        onClick={() => fileInputRef.current?.click()}
      >
        <Upload className="mr-2 size-4" />
        Upload JSON
      </Button>
    </div>
  )
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
                        <JsonCredentialInput field={formField} />
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
