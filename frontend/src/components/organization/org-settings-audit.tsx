"use client"

import { zodResolver } from "@hookform/resolvers/zod"
import { format } from "date-fns"
import { Copy, Key, Plus, RefreshCw, Settings2, Trash2, X } from "lucide-react"
import { useCallback, useEffect, useState } from "react"
import { useForm } from "react-hook-form"
import { z } from "zod"
import { CenteredSpinner } from "@/components/loading/spinner"
import { AlertNotification } from "@/components/notifications"
import {
  AlertDialog,
  AlertDialogAction,
  AlertDialogCancel,
  AlertDialogContent,
  AlertDialogDescription,
  AlertDialogFooter,
  AlertDialogHeader,
  AlertDialogTitle,
  AlertDialogTrigger,
} from "@/components/ui/alert-dialog"
import { Button } from "@/components/ui/button"
import {
  Card,
  CardContent,
  CardDescription,
  CardHeader,
  CardTitle,
} from "@/components/ui/card"
import {
  Dialog,
  DialogClose,
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
import { toast } from "@/components/ui/use-toast"
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
    generateAuditApiKey,
    generateAuditApiKeyIsPending,
    revokeAuditApiKey,
    revokeAuditApiKeyIsPending,
  } = useOrgAuditSettings()

  const [showNewApiKey, setShowNewApiKey] = useState(false)
  const [newApiKey, setNewApiKey] = useState<string | null>(null)
  const [customHeaders, setCustomHeaders] = useState<
    Array<{ key: string; value: string }>
  >([])
  const [customHeadersChanged, setCustomHeadersChanged] = useState(false)

  // Initialize custom headers from settings, but don't overwrite unsaved edits
  useEffect(() => {
    if (customHeadersChanged) {
      // User has unsaved edits, don't overwrite
      return
    }
    if (auditSettings?.audit_webhook_custom_headers) {
      const headers = Object.entries(
        auditSettings.audit_webhook_custom_headers
      ).map(([key, value]) => ({ key, value: String(value) }))
      setCustomHeaders(headers)
    } else {
      setCustomHeaders([])
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

  const handleGenerateApiKey = async () => {
    try {
      const response = await generateAuditApiKey()
      setNewApiKey(response.api_key)
      setShowNewApiKey(true)
    } catch {
      console.error("Failed to generate API key")
    }
  }

  const handleRevokeApiKey = async () => {
    try {
      await revokeAuditApiKey()
    } catch {
      console.error("Failed to revoke API key")
    }
  }

  const handleCopyApiKey = async () => {
    if (newApiKey) {
      await navigator.clipboard.writeText(newApiKey)
      toast({
        title: "Copied",
        description: "API key copied to clipboard.",
      })
    }
  }

  const handleCloseNewApiKeyDialog = () => {
    setShowNewApiKey(false)
    setNewApiKey(null)
  }

  const handleAddHeader = useCallback(() => {
    setCustomHeaders((prev) => [...prev, { key: "", value: "" }])
    setCustomHeadersChanged(true)
  }, [])

  const handleRemoveHeader = useCallback((index: number) => {
    setCustomHeaders((prev) => prev.filter((_, i) => i !== index))
    setCustomHeadersChanged(true)
  }, [])

  const handleHeaderChange = useCallback(
    (index: number, field: "key" | "value", newValue: string) => {
      setCustomHeaders((prev) =>
        prev.map((header, i) =>
          i === index ? { ...header, [field]: newValue } : header
        )
      )
      setCustomHeadersChanged(true)
    },
    []
  )

  const handleSaveCustomHeaders = async () => {
    // Filter out headers with empty keys
    const validHeaders = customHeaders.filter((h) => h.key.trim() !== "")

    // Convert array to object
    const headersObject: Record<string, string> = {}
    for (const header of validHeaders) {
      headersObject[header.key.trim()] = header.value
    }

    try {
      await updateAuditSettings({
        requestBody: {
          audit_webhook_custom_headers:
            Object.keys(headersObject).length > 0 ? headersObject : null,
        },
      })
      setCustomHeadersChanged(false)
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

  const hasApiKey = !!auditSettings.audit_webhook_api_key_preview

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
            <Key className="size-4" />
            API key authentication
          </CardTitle>
          <CardDescription>
            Optionally configure an API key to authenticate outgoing audit
            webhook requests. The key is sent as a Bearer token in the
            Authorization header.
          </CardDescription>
        </CardHeader>
        <CardContent className="space-y-4">
          {hasApiKey ? (
            <div className="space-y-4">
              <div className="flex items-center justify-between rounded-md border p-3">
                <div>
                  <p className="font-mono text-sm">
                    {auditSettings.audit_webhook_api_key_preview}
                  </p>
                  {auditSettings.audit_webhook_api_key_created_at && (
                    <p className="text-xs text-muted-foreground">
                      Created{" "}
                      {format(
                        new Date(
                          auditSettings.audit_webhook_api_key_created_at
                        ),
                        "MMM d, yyyy 'at' h:mm a"
                      )}
                    </p>
                  )}
                </div>
                <div className="flex gap-2">
                  <AlertDialog>
                    <AlertDialogTrigger asChild>
                      <Button
                        variant="outline"
                        size="sm"
                        disabled={generateAuditApiKeyIsPending}
                      >
                        <RefreshCw className="mr-2 size-3" />
                        Regenerate
                      </Button>
                    </AlertDialogTrigger>
                    <AlertDialogContent>
                      <AlertDialogHeader>
                        <AlertDialogTitle>Regenerate API key?</AlertDialogTitle>
                        <AlertDialogDescription>
                          This will replace the existing API key. Any systems
                          using the current key will need to be updated.
                        </AlertDialogDescription>
                      </AlertDialogHeader>
                      <AlertDialogFooter>
                        <AlertDialogCancel>Cancel</AlertDialogCancel>
                        <AlertDialogAction onClick={handleGenerateApiKey}>
                          {generateAuditApiKeyIsPending
                            ? "Regenerating..."
                            : "Regenerate"}
                        </AlertDialogAction>
                      </AlertDialogFooter>
                    </AlertDialogContent>
                  </AlertDialog>

                  <AlertDialog>
                    <AlertDialogTrigger asChild>
                      <Button
                        variant="outline"
                        size="sm"
                        disabled={revokeAuditApiKeyIsPending}
                      >
                        <Trash2 className="mr-2 size-3" />
                        Revoke
                      </Button>
                    </AlertDialogTrigger>
                    <AlertDialogContent>
                      <AlertDialogHeader>
                        <AlertDialogTitle>Revoke API key?</AlertDialogTitle>
                        <AlertDialogDescription>
                          This will remove the API key. Audit webhook requests
                          will no longer include authentication.
                        </AlertDialogDescription>
                      </AlertDialogHeader>
                      <AlertDialogFooter>
                        <AlertDialogCancel>Cancel</AlertDialogCancel>
                        <AlertDialogAction
                          onClick={handleRevokeApiKey}
                          className="bg-destructive text-destructive-foreground hover:bg-destructive/90"
                        >
                          {revokeAuditApiKeyIsPending
                            ? "Revoking..."
                            : "Revoke"}
                        </AlertDialogAction>
                      </AlertDialogFooter>
                    </AlertDialogContent>
                  </AlertDialog>
                </div>
              </div>
            </div>
          ) : (
            <Button
              variant="outline"
              onClick={handleGenerateApiKey}
              disabled={generateAuditApiKeyIsPending}
            >
              <Key className="mr-2 size-4" />
              {generateAuditApiKeyIsPending
                ? "Generating..."
                : "Generate API key"}
            </Button>
          )}
        </CardContent>
      </Card>

      <Card>
        <CardHeader>
          <CardTitle className="flex items-center gap-2 text-base">
            <Settings2 className="size-4" />
            Custom headers
          </CardTitle>
          <CardDescription>
            Add custom HTTP headers to include in audit webhook requests. Custom
            headers override the API key if both set an Authorization header.
            Header values are encrypted at rest.
          </CardDescription>
        </CardHeader>
        <CardContent className="space-y-4">
          {customHeaders.length > 0 ? (
            <div className="space-y-2">
              {customHeaders.map((header, index) => (
                <div key={index} className="flex items-center gap-2">
                  <Input
                    placeholder="Header name"
                    value={header.key}
                    onChange={(e) =>
                      handleHeaderChange(index, "key", e.target.value)
                    }
                    className="flex-1"
                  />
                  <Input
                    placeholder="Header value"
                    value={header.value}
                    onChange={(e) =>
                      handleHeaderChange(index, "value", e.target.value)
                    }
                    className="flex-1"
                  />
                  <Button
                    variant="ghost"
                    size="icon"
                    onClick={() => handleRemoveHeader(index)}
                    title="Remove header"
                  >
                    <X className="size-4" />
                  </Button>
                </div>
              ))}
            </div>
          ) : (
            <p className="text-sm text-muted-foreground">
              No custom headers configured.
            </p>
          )}
          <div className="flex gap-2">
            <Button variant="outline" size="sm" onClick={handleAddHeader}>
              <Plus className="mr-2 size-3" />
              Add header
            </Button>
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
          </div>
        </CardContent>
      </Card>

      <Dialog
        open={showNewApiKey}
        onOpenChange={(open) => {
          setShowNewApiKey(open)
          if (!open) {
            setNewApiKey(null)
          }
        }}
      >
        <DialogContent>
          <DialogHeader>
            <DialogTitle>API key generated</DialogTitle>
            <DialogDescription>
              Copy this key now. You will not be able to see it again.
            </DialogDescription>
          </DialogHeader>
          <div className="space-y-4">
            <div className="flex items-center gap-2">
              <Input
                readOnly
                value={newApiKey ?? ""}
                className="font-mono text-sm"
              />
              <Button
                variant="outline"
                size="icon"
                onClick={handleCopyApiKey}
                title="Copy to clipboard"
              >
                <Copy className="size-4" />
              </Button>
            </div>
          </div>
          <DialogFooter>
            <DialogClose asChild>
              <Button onClick={handleCloseNewApiKeyDialog}>Done</Button>
            </DialogClose>
          </DialogFooter>
        </DialogContent>
      </Dialog>
    </div>
  )
}
