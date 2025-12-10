"use client"

import { zodResolver } from "@hookform/resolvers/zod"
import { useForm } from "react-hook-form"
import { z } from "zod"
import { CenteredSpinner } from "@/components/loading/spinner"
import { AlertNotification } from "@/components/notifications"
import { Button } from "@/components/ui/button"
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
                  Provide an HTTPS endpoint to receive audit events. Leave blank
                  to disable streaming.
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
  )
}
