"use client"

import { zodResolver } from "@hookform/resolvers/zod"
import { Settings2 } from "lucide-react"
import { useEffect, useState } from "react"
import { useForm } from "react-hook-form"
import { z } from "zod"
import { CenteredSpinner } from "@/components/loading/spinner"
import { AlertNotification } from "@/components/notifications"
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
import { Textarea } from "@/components/ui/textarea"
import { useOrgAuditSettings } from "@/lib/hooks"

const auditFormSchema = z.object({
  audit_webhook_url: z
    .string()
    .trim()
    .url({ message: "Enter a valid URL (https://...)" })
    .max(2048)
    .or(z.literal("")),
})

type AuditFormValues = z.infer<typeof auditFormSchema>

export function OrgSettingsAuditForm() {
  const {
    auditSettings,
    auditSettingsIsLoading,
    auditSettingsError,
    updateAuditSettings,
    updateAuditSettingsIsPending,
  } = useOrgAuditSettings()

  const [customHeadersJson, setCustomHeadersJson] = useState("")
  const [customHeadersError, setCustomHeadersError] = useState<string | null>(
    null
  )
  const [customHeadersChanged, setCustomHeadersChanged] = useState(false)

  // Initialize custom headers JSON from settings, but don't overwrite unsaved edits
  useEffect(() => {
    if (customHeadersChanged) {
      return
    }
    if (auditSettings?.audit_webhook_custom_headers) {
      setCustomHeadersJson(
        JSON.stringify(auditSettings.audit_webhook_custom_headers, null, 2)
      )
    } else {
      setCustomHeadersJson("")
    }
  }, [auditSettings?.audit_webhook_custom_headers, customHeadersChanged])

  const form = useForm<AuditFormValues>({
    resolver: zodResolver(auditFormSchema),
    values: {
      audit_webhook_url: auditSettings?.audit_webhook_url ?? "",
    },
  })

  const onSubmit = async (data: AuditFormValues) => {
    const nextUrl = data.audit_webhook_url.trim()
    try {
      await updateAuditSettings({
        requestBody: {
          audit_webhook_url: nextUrl === "" ? null : nextUrl,
        },
      })
    } catch {
      console.error("Failed to update audit settings")
    }
  }

  const handleCustomHeadersChange = (value: string) => {
    setCustomHeadersJson(value)
    setCustomHeadersChanged(true)
    setCustomHeadersError(null)
  }

  const handleSaveCustomHeaders = async () => {
    const trimmed = customHeadersJson.trim()

    // Empty = clear headers
    if (trimmed === "") {
      try {
        await updateAuditSettings({
          requestBody: { audit_webhook_custom_headers: null },
        })
        setCustomHeadersChanged(false)
        setCustomHeadersError(null)
      } catch {
        console.error("Failed to update custom headers")
      }
      return
    }

    // Validate JSON
    let parsed: unknown
    try {
      parsed = JSON.parse(trimmed)
    } catch {
      setCustomHeadersError("Invalid JSON syntax")
      return
    }

    // Validate structure: must be object with string values
    if (
      typeof parsed !== "object" ||
      parsed === null ||
      Array.isArray(parsed)
    ) {
      setCustomHeadersError('Must be a JSON object, e.g. { "Header": "value" }')
      return
    }

    const headersObject = parsed as Record<string, unknown>
    for (const [key, value] of Object.entries(headersObject)) {
      if (typeof value !== "string") {
        setCustomHeadersError(`Value for "${key}" must be a string`)
        return
      }
    }

    try {
      await updateAuditSettings({
        requestBody: {
          audit_webhook_custom_headers: headersObject as Record<string, string>,
        },
      })
      setCustomHeadersChanged(false)
      setCustomHeadersError(null)
    } catch {
      console.error("Failed to update custom headers")
    }
  }

  if (auditSettingsIsLoading) {
    return <CenteredSpinner />
  }
  if (auditSettingsError || !auditSettings) {
    return (
      <AlertNotification
        level="error"
        message={`Error loading audit settings: ${auditSettingsError?.message || "Unknown error"}`}
      />
    )
  }

  return (
    <div className="space-y-8">
      <Form {...form}>
        <form onSubmit={form.handleSubmit(onSubmit)} className="space-y-8">
          <FormField
            control={form.control}
            name="audit_webhook_url"
            render={({ field }) => (
              <FormItem className="space-y-3">
                <div className="space-y-2">
                  <FormLabel>Audit webhook URL</FormLabel>
                  <FormDescription>
                    Provide an HTTPS endpoint to receive audit events. Leave
                    blank to disable streaming.
                  </FormDescription>
                </div>
                <FormControl>
                  <Input
                    type="url"
                    placeholder="https://example.com/webhooks/audit"
                    {...field}
                  />
                </FormControl>
                <FormMessage />
              </FormItem>
            )}
          />

          <Button type="submit" disabled={updateAuditSettingsIsPending}>
            {updateAuditSettingsIsPending
              ? "Updating..."
              : "Update audit settings"}
          </Button>
        </form>
      </Form>

      <Card>
        <CardHeader>
          <CardTitle className="flex items-center gap-2 text-base">
            <Settings2 className="size-4" />
            Custom headers
          </CardTitle>
          <CardDescription>
            Add custom HTTP headers to include in audit webhook requests. Header
            values are encrypted at rest.
          </CardDescription>
        </CardHeader>
        <CardContent className="space-y-4">
          <Textarea
            placeholder={'{\n  "X-Custom-Header": "value"\n}'}
            value={customHeadersJson}
            onChange={(e) => handleCustomHeadersChange(e.target.value)}
            className="min-h-[120px] font-mono text-sm"
          />
          {customHeadersError && (
            <p className="text-sm text-destructive">{customHeadersError}</p>
          )}
          {customHeadersChanged && (
            <Button
              size="sm"
              onClick={handleSaveCustomHeaders}
              disabled={updateAuditSettingsIsPending}
            >
              {updateAuditSettingsIsPending
                ? "Saving..."
                : "Save custom headers"}
            </Button>
          )}
        </CardContent>
      </Card>
    </div>
  )
}
