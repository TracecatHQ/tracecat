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
  app_secret_error_details_blocked_workspace_ids: z.array(z.string()),
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
      app_secret_error_details_blocked_workspace_ids:
        appSettings?.app_secret_error_details_blocked_workspace_ids ?? [],
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
          app_secret_error_details_blocked_workspace_ids:
            data.app_secret_error_details_blocked_workspace_ids,
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
          name="app_secret_error_details_blocked_workspace_ids"
          render={({ field }) => {
            const workspaceIds = workspaces?.map((ws) => ws.id) ?? []
            const blocked = new Set(field.value)
            const allAllowed = workspaceIds.every((id) => !blocked.has(id))
            const allBlocked =
              workspaceIds.length > 0 &&
              workspaceIds.every((id) => blocked.has(id))
            const allowedCount = workspaceIds.filter(
              (id) => !blocked.has(id)
            ).length
            return (
              <FormItem className="rounded-lg border p-4">
                <div className="space-y-0.5">
                  <FormLabel>Show error details</FormLabel>
                  <FormDescription>
                    Unsafe: actions in allowed workspaces can opt into showing
                    original error messages when secrets are in scope. All
                    workspaces are allowed by default. Known secret values are
                    always masked.
                  </FormDescription>
                </div>
                <div className="pt-4">
                  <div className="flex items-center justify-between pb-2">
                    <span className="text-xs text-muted-foreground">
                      {workspacesLoading
                        ? "Loading workspaces..."
                        : `${allowedCount} of ${workspaceIds.length} workspaces allowed`}
                    </span>
                    <div className="flex gap-1">
                      <Button
                        type="button"
                        variant="ghost"
                        size="sm"
                        className="h-7 text-xs"
                        disabled={workspacesLoading || allAllowed}
                        onClick={() => field.onChange([])}
                      >
                        Allow all
                      </Button>
                      <Button
                        type="button"
                        variant="ghost"
                        size="sm"
                        className="h-7 text-xs"
                        disabled={workspacesLoading || allBlocked}
                        onClick={() => field.onChange(workspaceIds)}
                      >
                        Disable all
                      </Button>
                    </div>
                  </div>
                  <div className="divide-y rounded-md border">
                    {!workspacesLoading && workspaceIds.length === 0 && (
                      <p className="p-3 text-xs text-muted-foreground">
                        No workspaces found.
                      </p>
                    )}
                    {workspaces?.map((workspace) => {
                      const allowed = !blocked.has(workspace.id)
                      return (
                        <div
                          key={workspace.id}
                          className="flex items-center justify-between px-3 py-2"
                        >
                          <span className="text-sm">{workspace.name}</span>
                          <div className="flex items-center gap-2">
                            <span className="text-xs text-muted-foreground">
                              {allowed ? "Allowed" : "Opted out"}
                            </span>
                            <Switch
                              checked={allowed}
                              onCheckedChange={(next) => {
                                if (next) {
                                  field.onChange(
                                    field.value.filter(
                                      (id) => id !== workspace.id
                                    )
                                  )
                                } else {
                                  field.onChange([...field.value, workspace.id])
                                }
                              }}
                            />
                          </div>
                        </div>
                      )
                    })}
                  </div>
                </div>
              </FormItem>
            )
          }}
        />

        <Button type="submit" disabled={updateAppSettingsIsPending}>
          Update application settings
        </Button>
      </form>
    </Form>
  )
}
