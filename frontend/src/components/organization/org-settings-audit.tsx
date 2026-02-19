"use client"

import { zodResolver } from "@hookform/resolvers/zod"
import {
  AlertTriangleIcon,
  CheckCircle2Icon,
  LogsIcon,
  PlusIcon,
  Trash2Icon,
  UnlinkIcon,
} from "lucide-react"
import { useState } from "react"
import { useFieldArray, useForm } from "react-hook-form"
import { z } from "zod"
import { CodeEditor } from "@/components/editor/codemirror/code-editor"
import { CenteredSpinner } from "@/components/loading/spinner"
import { AlertNotification } from "@/components/notifications"
import { Alert, AlertDescription, AlertTitle } from "@/components/ui/alert"
import { Button } from "@/components/ui/button"
import {
  Dialog,
  DialogContent,
  DialogDescription,
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
import { ScrollArea } from "@/components/ui/scroll-area"
import { Switch } from "@/components/ui/switch"
import { useOrgAuditSettings } from "@/lib/hooks"

const headerFormSchema = z.object({
  key: z.string(),
  value: z.string(),
})

const auditDialogFormSchema = z
  .object({
    audit_webhook_url: z
      .string()
      .trim()
      .url({ message: "Enter a valid URL (https://...)" })
      .max(2048)
      .or(z.literal("")),
    headers: z.array(headerFormSchema),
    audit_webhook_payload_attribute: z
      .string()
      .trim()
      .max(128, "Payload attribute must be 128 characters or fewer")
      .or(z.literal("")),
    custom_payload_json: z.string(),
    audit_webhook_verify_ssl: z.boolean(),
  })
  .superRefine((values, ctx) => {
    const seenHeaders = new Map<string, number>()

    values.headers.forEach((header, index) => {
      const headerKey = header.key.trim()
      const headerValue = header.value

      if (headerKey === "" && headerValue.trim() === "") {
        return
      }
      if (headerKey === "") {
        ctx.addIssue({
          code: z.ZodIssueCode.custom,
          message: "Header name is required",
          path: ["headers", index, "key"],
        })
        return
      }
      if (headerValue.trim() === "") {
        ctx.addIssue({
          code: z.ZodIssueCode.custom,
          message: "Header value is required",
          path: ["headers", index, "value"],
        })
        return
      }

      const normalizedHeaderKey = headerKey.toLowerCase()
      const existingHeaderIndex = seenHeaders.get(normalizedHeaderKey)
      if (existingHeaderIndex !== undefined) {
        ctx.addIssue({
          code: z.ZodIssueCode.custom,
          message: "Duplicate header name",
          path: ["headers", index, "key"],
        })
        return
      }
      seenHeaders.set(normalizedHeaderKey, index)
    })

    const trimmedPayload = values.custom_payload_json.trim()
    if (trimmedPayload === "") {
      return
    }

    try {
      const parsedPayload: unknown = JSON.parse(trimmedPayload)
      if (
        typeof parsedPayload !== "object" ||
        parsedPayload === null ||
        Array.isArray(parsedPayload)
      ) {
        throw new Error("payload must be a JSON object")
      }
    } catch {
      ctx.addIssue({
        code: z.ZodIssueCode.custom,
        message:
          'Custom payload must be a JSON object, e.g. { "event": "audit" }',
        path: ["custom_payload_json"],
      })
    }
  })

type AuditDialogFormValues = z.infer<typeof auditDialogFormSchema>
type HeaderEntry = AuditDialogFormValues["headers"][number]

function toHeaderEntries(
  headers: Record<string, string> | null | undefined
): HeaderEntry[] {
  if (!headers) {
    return []
  }
  return Object.entries(headers).map(([key, value]) => ({ key, value }))
}

function normalizeCustomHeaders(
  headers: Record<string, string> | null | undefined
): Record<string, string> {
  if (!headers) {
    return {}
  }

  const entries = Object.entries(headers)
  if (entries.length !== 1) {
    return headers
  }

  const [headerKey, headerValue] = entries[0]
  if (
    headerKey.trim().toLowerCase() === "hello" &&
    headerValue.trim().toLowerCase() === "world"
  ) {
    return {}
  }

  return headers
}

function parseCustomPayload(
  customPayloadJson: string
): Record<string, unknown> | null {
  const trimmedPayload = customPayloadJson.trim()
  if (trimmedPayload === "") {
    return null
  }

  const parsedPayload: unknown = JSON.parse(trimmedPayload)
  if (
    typeof parsedPayload !== "object" ||
    parsedPayload === null ||
    Array.isArray(parsedPayload)
  ) {
    throw new Error("Custom payload must be a JSON object")
  }
  return parsedPayload as Record<string, unknown>
}

function formatCustomPayload(
  payload: Record<string, unknown> | null | undefined
): string {
  if (!payload) {
    return ""
  }
  return JSON.stringify(payload, null, 2)
}

function normalizePayloadAttribute(value: string | null | undefined): string {
  return value?.trim() ?? ""
}

function maskWebhookUrl(url: string): string {
  const trimmed = url.trim()
  if (trimmed === "") {
    return trimmed
  }

  try {
    const parsedUrl = new URL(trimmed)
    const pathSegments = parsedUrl.pathname.split("/").filter(Boolean)
    const lastPathSegment = pathSegments.at(-1)
    if (!lastPathSegment) {
      return `${parsedUrl.origin}/...`
    }
    return `${parsedUrl.origin}/.../${lastPathSegment}`
  } catch {
    if (trimmed.length <= 24) {
      return trimmed
    }
    return `${trimmed.slice(0, 18)}...${trimmed.slice(-6)}`
  }
}

export function OrgSettingsAuditForm() {
  const {
    auditSettings,
    auditSettingsIsLoading,
    auditSettingsError,
    updateAuditSettings,
    updateAuditSettingsIsPending,
  } = useOrgAuditSettings()
  const [dialogOpen, setDialogOpen] = useState(false)

  const form = useForm<AuditDialogFormValues>({
    resolver: zodResolver(auditDialogFormSchema),
    defaultValues: {
      audit_webhook_url: "",
      headers: [],
      audit_webhook_payload_attribute: "",
      custom_payload_json: "",
      audit_webhook_verify_ssl: true,
    },
  })
  const {
    fields: headerFields,
    append: appendHeader,
    remove: removeHeader,
  } = useFieldArray({
    control: form.control,
    name: "headers",
  })

  const onSubmit = async (data: AuditDialogFormValues) => {
    const nextUrl = data.audit_webhook_url.trim()
    const nextHeaders = data.headers.reduce<Record<string, string>>(
      (headers, header) => {
        const headerKey = header.key.trim()
        if (headerKey !== "" && header.value.trim() !== "") {
          headers[headerKey] = header.value
        }
        return headers
      },
      {}
    )

    let customPayload: Record<string, unknown> | null
    try {
      customPayload = parseCustomPayload(data.custom_payload_json)
    } catch {
      form.setError("custom_payload_json", {
        message:
          'Custom payload must be a JSON object, e.g. { "event": "audit" }',
      })
      return
    }

    try {
      await updateAuditSettings({
        requestBody: {
          audit_webhook_url: nextUrl === "" ? null : nextUrl,
          audit_webhook_custom_headers:
            Object.keys(nextHeaders).length > 0 ? nextHeaders : null,
          audit_webhook_payload_attribute:
            data.audit_webhook_payload_attribute.trim() === ""
              ? null
              : data.audit_webhook_payload_attribute.trim(),
          audit_webhook_custom_payload: customPayload,
          audit_webhook_verify_ssl: data.audit_webhook_verify_ssl,
        },
      })
      setDialogOpen(false)
    } catch {
      console.error("Failed to update audit settings")
    }
  }

  const handleDisconnect = async () => {
    try {
      await updateAuditSettings({
        requestBody: {
          audit_webhook_url: null,
          audit_webhook_custom_headers: null,
          audit_webhook_payload_attribute: null,
          audit_webhook_custom_payload: null,
          audit_webhook_verify_ssl: true,
        },
      })
      setDialogOpen(false)
    } catch {
      console.error("Failed to reset audit settings")
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

  const failedKeys = auditSettings.decryption_failed_keys ?? []
  const currentWebhookUrl = auditSettings.audit_webhook_url?.trim() ?? ""
  const currentHeaders = normalizeCustomHeaders(
    auditSettings.audit_webhook_custom_headers
  )
  const currentCustomPayload =
    auditSettings.audit_webhook_custom_payload ?? null
  const currentPayloadAttribute = normalizePayloadAttribute(
    auditSettings.audit_webhook_payload_attribute
  )
  const currentVerifySsl = auditSettings.audit_webhook_verify_ssl ?? true
  const customHeaderCount = Object.keys(currentHeaders).length
  const hasCustomPayload =
    currentCustomPayload !== null &&
    Object.keys(currentCustomPayload).length > 0
  const isConnected = currentWebhookUrl !== ""

  const connectionDescription = (() => {
    if (!isConnected) {
      return "Not connected"
    }

    const details = [maskWebhookUrl(currentWebhookUrl)]
    if (customHeaderCount > 0) {
      const suffix = customHeaderCount === 1 ? "header" : "headers"
      details.push(`${customHeaderCount} custom ${suffix}`)
    }
    if (hasCustomPayload) {
      details.push("Custom payload")
    }
    if (currentPayloadAttribute !== "") {
      details.push(`Payload key: ${currentPayloadAttribute}`)
    }
    if (!currentVerifySsl) {
      details.push("SSL verify off")
    }
    return details.join(" · ")
  })()

  const handleDialogOpenChange = (open: boolean) => {
    if (open) {
      form.reset({
        audit_webhook_url: currentWebhookUrl,
        headers: toHeaderEntries(currentHeaders),
        custom_payload_json: formatCustomPayload(currentCustomPayload),
        audit_webhook_payload_attribute: currentPayloadAttribute,
        audit_webhook_verify_ssl: currentVerifySsl,
      })
    }
    setDialogOpen(open)
  }

  return (
    <>
      {failedKeys.length > 0 && (
        <Alert>
          <AlertTriangleIcon className="size-4 !text-destructive" />
          <AlertTitle className="text-destructive">
            Unable to decrypt organization settings
          </AlertTitle>
          <AlertDescription>
            Failed to decrypt existing values for {failedKeys.join(", ")}.
            Please reconfigure and save these settings again.
          </AlertDescription>
        </Alert>
      )}

      <div className="flex items-center justify-between rounded-lg border p-4">
        <div className="flex items-center gap-3">
          {isConnected ? (
            <CheckCircle2Icon className="size-5 text-green-500" />
          ) : (
            <LogsIcon className="size-5 text-muted-foreground" />
          )}
          <div>
            <p className="text-sm font-medium">Audit logs endpoint</p>
            <p className="text-xs text-muted-foreground">
              {connectionDescription}
            </p>
          </div>
        </div>
        {isConnected ? (
          <div className="flex items-center gap-2">
            <Button size="sm" onClick={() => handleDialogOpenChange(true)}>
              Update
            </Button>
            <Button
              type="button"
              variant="destructive"
              size="sm"
              onClick={handleDisconnect}
              disabled={updateAuditSettingsIsPending}
            >
              <UnlinkIcon className="size-3.5" />
              <span className="sr-only">Disconnect audit logs endpoint</span>
            </Button>
          </div>
        ) : (
          <Button size="sm" onClick={() => handleDialogOpenChange(true)}>
            Connect
          </Button>
        )}
      </div>

      <Dialog open={dialogOpen} onOpenChange={handleDialogOpenChange}>
        <DialogContent className="flex max-h-[90vh] flex-col overflow-hidden sm:max-w-xl">
          <DialogHeader>
            <DialogTitle>
              {isConnected ? "Update audit webhook" : "Connect audit webhook"}
            </DialogTitle>
            <DialogDescription>
              Configure where audit events are delivered and set optional
              request options.
            </DialogDescription>
          </DialogHeader>
          <ScrollArea className="flex-1 pr-1">
            <Form {...form}>
              <form
                onSubmit={form.handleSubmit(onSubmit)}
                className="space-y-6"
              >
                <FormField
                  control={form.control}
                  name="audit_webhook_url"
                  render={({ field }) => (
                    <FormItem className="space-y-2">
                      <FormLabel>Audit webhook URL</FormLabel>
                      <FormDescription className="text-xs">
                        Provide an HTTPS endpoint to receive audit events.
                      </FormDescription>
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

                <div className="space-y-3">
                  <div className="space-y-1.5">
                    <p className="text-sm font-medium">Custom headers</p>
                    <p className="text-xs text-muted-foreground">
                      POST request headers.
                    </p>
                  </div>

                  {headerFields.length === 0 && (
                    <p className="text-xs text-muted-foreground">
                      No custom headers configured.
                    </p>
                  )}

                  {headerFields.map((field, index) => (
                    <div
                      key={field.id}
                      className="grid grid-cols-[1fr_1fr_auto] gap-2"
                    >
                      <FormField
                        control={form.control}
                        name={`headers.${index}.key` as const}
                        render={({ field: headerField }) => (
                          <FormItem>
                            <FormControl>
                              <Input
                                {...headerField}
                                placeholder="X-Custom-Header"
                                autoComplete="off"
                              />
                            </FormControl>
                            <FormMessage />
                          </FormItem>
                        )}
                      />
                      <FormField
                        control={form.control}
                        name={`headers.${index}.value` as const}
                        render={({ field: valueField }) => (
                          <FormItem>
                            <FormControl>
                              <Input
                                {...valueField}
                                type="password"
                                placeholder="Header value"
                                autoComplete="off"
                              />
                            </FormControl>
                            <FormMessage />
                          </FormItem>
                        )}
                      />
                      <Button
                        type="button"
                        variant="ghost"
                        size="icon"
                        onClick={() => removeHeader(index)}
                        className="mt-0.5"
                      >
                        <Trash2Icon className="size-4" />
                        <span className="sr-only">Remove header</span>
                      </Button>
                    </div>
                  ))}

                  <Button
                    type="button"
                    variant="outline"
                    size="sm"
                    onClick={() => appendHeader({ key: "", value: "" })}
                    className="gap-2"
                  >
                    <PlusIcon className="size-4" />
                    Add header
                  </Button>
                </div>

                <FormField
                  control={form.control}
                  name="custom_payload_json"
                  render={({ field }) => (
                    <FormItem className="space-y-2">
                      <FormLabel>Custom payload (optional)</FormLabel>
                      <FormDescription>
                        JSON object merged into the default audit event payload.
                      </FormDescription>
                      <FormControl>
                        <CodeEditor
                          value={field.value}
                          onChange={field.onChange}
                          language="json"
                          className="font-mono text-xs [&_.cm-content]:text-xs [&_.cm-editor]:min-h-[120px]"
                        />
                      </FormControl>
                      <FormMessage />
                    </FormItem>
                  )}
                />

                <FormField
                  control={form.control}
                  name="audit_webhook_payload_attribute"
                  render={({ field }) => (
                    <FormItem className="space-y-2">
                      <FormLabel>Payload attribute (optional)</FormLabel>
                      <FormDescription className="text-xs">
                        Wrap payload under this key (e.g. <code>event</code>).
                      </FormDescription>
                      <FormControl>
                        <Input
                          {...field}
                          placeholder="event"
                          autoComplete="off"
                        />
                      </FormControl>
                      <FormMessage />
                    </FormItem>
                  )}
                />

                <FormField
                  control={form.control}
                  name="audit_webhook_verify_ssl"
                  render={({ field }) => (
                    <FormItem className="flex flex-row items-center justify-between rounded-lg border p-4">
                      <FormLabel>Verify SSL certificate</FormLabel>
                      <FormControl>
                        <Switch
                          checked={field.value}
                          onCheckedChange={field.onChange}
                        />
                      </FormControl>
                    </FormItem>
                  )}
                />

                <Button type="submit" disabled={updateAuditSettingsIsPending}>
                  {updateAuditSettingsIsPending ? "Saving..." : "Save changes"}
                </Button>
              </form>
            </Form>
          </ScrollArea>
        </DialogContent>
      </Dialog>
    </>
  )
}
