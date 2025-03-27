"use client"

import { zodResolver } from "@hookform/resolvers/zod"
import { useForm } from "react-hook-form"
import { z } from "zod"

import { useOrgAppSettings } from "@/lib/hooks"
import { Button } from "@/components/ui/button"
import {
  Form,
  FormControl,
  FormDescription,
  FormField,
  FormItem,
  FormLabel,
} from "@/components/ui/form"
import { Input } from "@/components/ui/input"
import { Switch } from "@/components/ui/switch"
import { CenteredSpinner } from "@/components/loading/spinner"
import { AlertNotification } from "@/components/notifications"

const appFormSchema = z.object({
  app_registry_validation_enabled: z.boolean(),
  app_executions_query_limit: z.number().min(1).max(1000),
})

type AppFormValues = z.infer<typeof appFormSchema>

export function OrgSettingsAppForm() {
  const {
    appSettings,
    appSettingsIsLoading,
    appSettingsError,
    updateAppSettings,
    updateAppSettingsIsPending,
  } = useOrgAppSettings()

  const form = useForm<AppFormValues>({
    resolver: zodResolver(appFormSchema),
    values: {
      app_registry_validation_enabled:
        appSettings?.app_registry_validation_enabled ?? false,
      app_executions_query_limit:
        appSettings?.app_executions_query_limit ?? 100,
    },
  })

  const onSubmit = async (data: AppFormValues) => {
    try {
      await updateAppSettings({
        requestBody: {
          app_registry_validation_enabled: data.app_registry_validation_enabled,
          app_executions_query_limit: data.app_executions_query_limit,
        },
      })
    } catch {
      console.error("Failed to update App settings")
    }
  }

  if (appSettingsIsLoading) {
    return <CenteredSpinner />
  }
  if (appSettingsError || !appSettings) {
    return (
      <AlertNotification
        level="error"
        message={`Error loading App settings: ${appSettingsError?.message || "Unknown error"}`}
      />
    )
  }

  return (
    <Form {...form}>
      <form onSubmit={form.handleSubmit(onSubmit)} className="space-y-8">
        <FormField
          control={form.control}
          name="app_registry_validation_enabled"
          render={({ field }) => (
            <FormItem className="flex flex-row items-center justify-between rounded-lg border p-4">
              <div className="space-y-0.5">
                <FormLabel>Enable registry validation</FormLabel>
                <FormDescription>
                  Enable registry validation (alpha).
                </FormDescription>
              </div>
              <FormControl>
                <Switch
                  checked={field.value}
                  onCheckedChange={field.onChange}
                />
              </FormControl>
            </FormItem>
          )}
        />

        <FormField
          control={form.control}
          name="app_executions_query_limit"
          render={({ field }) => (
            <FormItem className="flex flex-row items-center justify-between rounded-lg border p-4">
              <div className="space-y-0.5">
                <FormLabel>Workflow Executions Query Limit</FormLabel>
                <FormDescription>
                  Maximum number of executions that can be queried at once.
                </FormDescription>
              </div>
              <FormControl>
                <Input
                  type="number"
                  min={1}
                  max={1000}
                  {...field}
                  onChange={(e) => field.onChange(Number(e.target.value))}
                  className="w-24"
                />
              </FormControl>
            </FormItem>
          )}
        />

        <Button type="submit" disabled={updateAppSettingsIsPending}>
          Update application settings
        </Button>
      </form>
    </Form>
  )
}
