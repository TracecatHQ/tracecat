"use client"

import { zodResolver } from "@hookform/resolvers/zod"
import { useForm } from "react-hook-form"
import { z } from "zod"
import type { WorkspaceRead } from "@/client"
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
import { useWorkspaceSettings } from "@/lib/hooks"

export const runtimeSettingsSchema = z.object({
  workflow_unlimited_timeout_enabled: z.boolean().optional(),
  workflow_default_timeout_seconds: z
    .number()
    .min(1, "Timeout must be at least 1 second")
    .optional(),
})

type RuntimeSettingsForm = z.infer<typeof runtimeSettingsSchema>

interface WorkspaceRuntimeSettingsProps {
  workspace: WorkspaceRead
}

export function buildRuntimeSettingsUpdate(values: RuntimeSettingsForm) {
  return {
    workflow_unlimited_timeout_enabled:
      values.workflow_unlimited_timeout_enabled,
    workflow_default_timeout_seconds:
      values.workflow_default_timeout_seconds ?? null,
  }
}

export function WorkspaceRuntimeSettings({
  workspace,
}: WorkspaceRuntimeSettingsProps) {
  const { updateWorkspace, isUpdating } = useWorkspaceSettings(workspace.id)

  const form = useForm<RuntimeSettingsForm>({
    resolver: zodResolver(runtimeSettingsSchema),
    mode: "onChange",
    defaultValues: {
      workflow_unlimited_timeout_enabled:
        workspace.settings?.workflow_unlimited_timeout_enabled ?? false,
      workflow_default_timeout_seconds:
        workspace.settings?.workflow_default_timeout_seconds || undefined,
    },
  })

  async function onSubmit(values: RuntimeSettingsForm) {
    await updateWorkspace({
      settings: buildRuntimeSettingsUpdate(values),
    })
  }

  return (
    <Form {...form}>
      <form onSubmit={form.handleSubmit(onSubmit)} className="space-y-6">
        <FormField
          control={form.control}
          name="workflow_unlimited_timeout_enabled"
          render={({ field }) => (
            <FormItem className="flex flex-row items-center justify-between rounded-lg border p-4">
              <div className="space-y-0.5">
                <FormLabel>Unlimited workflow timeout</FormLabel>
                <FormDescription>
                  Force all workflows to run indefinitely without timeout
                  constraints.
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
          name="workflow_default_timeout_seconds"
          render={({ field }) => (
            <FormItem className="flex flex-row items-center justify-between rounded-lg border p-4">
              <div className="space-y-0.5">
                <FormLabel>Default workflow timeout</FormLabel>
                <FormDescription>
                  Default timeout in seconds for workflows in this workspace.
                  Disabled if unlimited timeout is enabled.
                </FormDescription>
              </div>
              <FormControl>
                <Input
                  type="number"
                  min={1}
                  placeholder="300"
                  {...field}
                  value={field.value ?? ""}
                  onChange={(e) =>
                    field.onChange(
                      e.target.value ? Number(e.target.value) : undefined
                    )
                  }
                  disabled={form.watch("workflow_unlimited_timeout_enabled")}
                  className="w-24"
                />
              </FormControl>
            </FormItem>
          )}
        />

        <Button type="submit" disabled={isUpdating} size="sm">
          {isUpdating ? "Saving..." : "Save"}
        </Button>
      </form>
    </Form>
  )
}
