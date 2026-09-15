"use client"

import { zodResolver } from "@hookform/resolvers/zod"
import { useForm } from "react-hook-form"
import { z } from "zod"
import { CenteredSpinner } from "@/components/loading/spinner"
import { AlertNotification } from "@/components/notifications"
import { Button } from "@/components/ui/button"
import { Checkbox } from "@/components/ui/checkbox"
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
import { useOrgAppSettings, useWorkspaceManager } from "@/lib/hooks"

const appFormSchema = z.object({
  app_registry_validation_enabled: z.boolean(),
  app_executions_query_limit: z.number().min(1).max(1000),
  app_interactions_enabled: z.boolean(),
  app_workflow_export_enabled: z.boolean(),
  app_create_workspace_on_register: z.boolean(),
  app_action_form_mode_enabled: z.boolean(),
  app_unsafe_disable_secret_error_withholding_workspace_ids: z.array(
    z.string()
  ),
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
      app_interactions_enabled: appSettings?.app_interactions_enabled ?? false,
      app_workflow_export_enabled:
        appSettings?.app_workflow_export_enabled ?? true,
      app_create_workspace_on_register:
        appSettings?.app_create_workspace_on_register ?? false,
      app_action_form_mode_enabled:
        appSettings?.app_action_form_mode_enabled ?? true,
      app_unsafe_disable_secret_error_withholding_workspace_ids:
        appSettings?.app_unsafe_disable_secret_error_withholding_workspace_ids ??
        [],
    },
  })
  const { workspaces, workspacesLoading } = useWorkspaceManager()

  const onSubmit = async (data: AppFormValues) => {
    try {
      await updateAppSettings({
        requestBody: {
          app_registry_validation_enabled: data.app_registry_validation_enabled,
          app_executions_query_limit: data.app_executions_query_limit,
          app_interactions_enabled: data.app_interactions_enabled,
          app_workflow_export_enabled: data.app_workflow_export_enabled,
          app_create_workspace_on_register:
            data.app_create_workspace_on_register,
          app_action_form_mode_enabled: data.app_action_form_mode_enabled,
          app_unsafe_disable_secret_error_withholding_workspace_ids:
            data.app_unsafe_disable_secret_error_withholding_workspace_ids,
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
                <FormDescription>Enable registry validation.</FormDescription>
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
                <FormLabel>Workflow executions query limit</FormLabel>
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

        <FormField
          control={form.control}
          name="app_interactions_enabled"
          render={({ field }) => (
            <FormItem className="flex flex-row items-center justify-between rounded-lg border p-4">
              <div className="space-y-0.5">
                <FormLabel>Enable interactions</FormLabel>
                <FormDescription>
                  Enable application interactions functionality.
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
          name="app_workflow_export_enabled"
          render={({ field }) => (
            <FormItem className="flex flex-row items-center justify-between rounded-lg border p-4">
              <div className="space-y-0.5">
                <FormLabel>Enable workflow exports</FormLabel>
                <FormDescription>
                  Allow users to export workflows. When disabled, users will be
                  unable to export workflows.
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
          name="app_create_workspace_on_register"
          render={({ field }) => (
            <FormItem className="flex flex-row items-center justify-between rounded-lg border p-4">
              <div className="space-y-0.5">
                <FormLabel>Create workspace on sign-up</FormLabel>
                <FormDescription>
                  Automatically create a workspace for new users when they sign
                  up.
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
          name="app_action_form_mode_enabled"
          render={({ field }) => (
            <FormItem className="flex flex-row items-center justify-between rounded-lg border p-4">
              <div className="space-y-0.5">
                <FormLabel>Enable action form mode</FormLabel>
                <FormDescription>
                  Allow form mode for action inputs. When disabled, only YAML
                  mode is available.
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
          name="app_unsafe_disable_secret_error_withholding_workspace_ids"
          render={({ field }) => (
            <FormItem className="rounded-lg border p-4">
              <div className="space-y-0.5">
                <FormLabel>Workspaces allowed to show error details</FormLabel>
                <FormDescription>
                  Unsafe: actions in these workspaces can opt into showing
                  original error messages when secrets are in scope. Known
                  secret values are still masked.
                </FormDescription>
              </div>
              <div className="space-y-2 pt-2">
                {workspacesLoading && (
                  <p className="text-xs text-muted-foreground">
                    Loading workspaces...
                  </p>
                )}
                {!workspacesLoading && (workspaces?.length ?? 0) === 0 && (
                  <p className="text-xs text-muted-foreground">
                    No workspaces found.
                  </p>
                )}
                {workspaces?.map((workspace) => {
                  const checked = field.value.includes(workspace.id)
                  return (
                    <label
                      key={workspace.id}
                      className="flex items-center gap-2 text-sm"
                    >
                      <Checkbox
                        checked={checked}
                        onCheckedChange={(next) => {
                          if (next === true) {
                            field.onChange([...field.value, workspace.id])
                          } else {
                            field.onChange(
                              field.value.filter((id) => id !== workspace.id)
                            )
                          }
                        }}
                      />
                      <span>{workspace.name}</span>
                    </label>
                  )
                })}
              </div>
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
