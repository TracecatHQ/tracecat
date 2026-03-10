"use client"

import { zodResolver } from "@hookform/resolvers/zod"
import { useMemo, useState } from "react"
import { useForm } from "react-hook-form"
import { z } from "zod"
import type { WorkspaceRead } from "@/client"
import { ProviderIcon } from "@/components/icons"
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
import { ScrollArea } from "@/components/ui/scroll-area"
import { Switch } from "@/components/ui/switch"
import { useAgentModels, useWorkspaceSettings } from "@/lib/hooks"
import { cn } from "@/lib/utils"

export const workspaceModelSettingsSchema = z.object({
  inherit_agent_enabled_models: z.boolean().default(true),
  agent_enabled_model_refs: z.array(z.string()).default([]),
})

type WorkspaceModelSettingsForm = z.infer<typeof workspaceModelSettingsSchema>

function getProviderIconId(provider: string): string {
  switch (provider) {
    case "azure_ai":
    case "azure_openai":
      return "microsoft"
    case "bedrock":
      return "amazon-bedrock"
    case "gemini":
    case "vertex_ai":
      return "google"
    case "openai":
      return "openai"
    case "anthropic":
      return "anthropic"
    default:
      return "custom"
  }
}

export function buildWorkspaceModelSettingsUpdate(
  values: WorkspaceModelSettingsForm
) {
  return {
    agent_enabled_model_refs: values.inherit_agent_enabled_models
      ? null
      : values.agent_enabled_model_refs,
  }
}

interface WorkspaceModelSettingsProps {
  workspace: WorkspaceRead
}

export function WorkspaceModelSettings({
  workspace,
}: WorkspaceModelSettingsProps) {
  const [modelQuery, setModelQuery] = useState("")
  const workspaceSettings = (workspace.settings ?? null) as
    | (WorkspaceRead["settings"] & {
        agent_enabled_model_refs?: string[] | null
      })
    | null
  const workspaceModelRefs = workspaceSettings?.agent_enabled_model_refs ?? null
  const { updateWorkspace, isUpdating } = useWorkspaceSettings(workspace.id)
  const { models, modelsLoading } = useAgentModels()

  const form = useForm<WorkspaceModelSettingsForm>({
    resolver: zodResolver(workspaceModelSettingsSchema),
    mode: "onChange",
    defaultValues: {
      inherit_agent_enabled_models: workspaceModelRefs === null,
      agent_enabled_model_refs: workspaceModelRefs ?? [],
    },
  })

  const inheritOrgModels = form.watch("inherit_agent_enabled_models")
  const filteredModels = useMemo(() => {
    const query = modelQuery.trim().toLowerCase()
    if (!query) {
      return models ?? []
    }
    return (
      models?.filter((model) => {
        return (
          model.display_name.toLowerCase().includes(query) ||
          model.model_name.toLowerCase().includes(query) ||
          model.runtime_provider.toLowerCase().includes(query) ||
          model.source_name.toLowerCase().includes(query)
        )
      }) ?? []
    )
  }, [modelQuery, models])

  async function onSubmit(values: WorkspaceModelSettingsForm) {
    await updateWorkspace({
      settings: buildWorkspaceModelSettingsUpdate(values),
    })
  }

  return (
    <Form {...form}>
      <form onSubmit={form.handleSubmit(onSubmit)} className="space-y-6">
        <div className="space-y-1">
          <h3 className="text-lg font-medium">Enabled AI models</h3>
          <p className="text-sm text-muted-foreground">
            Organization settings define the shared enabled catalog. This
            workspace can inherit all of it or use a narrower subset.
          </p>
        </div>

        <FormField
          control={form.control}
          name="inherit_agent_enabled_models"
          render={({ field }) => (
            <FormItem className="flex flex-row items-center justify-between rounded-lg border p-4">
              <div className="space-y-0.5">
                <FormLabel>Inherit organization-enabled models</FormLabel>
                <FormDescription>
                  When enabled, this workspace can use every model the
                  organization has enabled.
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
          name="agent_enabled_model_refs"
          render={({ field }) => (
            <FormItem className="space-y-3 rounded-lg border p-4">
              <div className="space-y-0.5">
                <FormLabel>Workspace model subset</FormLabel>
                <FormDescription>
                  Choose which organization-enabled models remain available in
                  this workspace.
                </FormDescription>
              </div>

              {modelsLoading ? (
                <p className="text-sm text-muted-foreground">
                  Loading enabled models...
                </p>
              ) : models?.length ? (
                <div className="space-y-3">
                  <Input
                    disabled={inheritOrgModels}
                    onChange={(event) => {
                      setModelQuery(event.target.value)
                    }}
                    placeholder="Search organization-enabled models"
                    value={modelQuery}
                  />
                  <ScrollArea className="h-80 rounded-lg border">
                    <div className="divide-y">
                      {filteredModels.length ? (
                        filteredModels.map((model) => {
                          const selected = (field.value ?? []).includes(
                            model.catalog_ref
                          )
                          const checked = inheritOrgModels ? true : selected

                          return (
                            <label
                              key={model.catalog_ref}
                              className={cn(
                                "flex items-start gap-3 px-4 py-3 transition-colors",
                                inheritOrgModels
                                  ? "cursor-not-allowed opacity-60"
                                  : "cursor-pointer hover:bg-muted/30"
                              )}
                            >
                              <Checkbox
                                checked={checked}
                                disabled={inheritOrgModels}
                                onCheckedChange={(nextChecked) => {
                                  const current = field.value ?? []
                                  if (nextChecked) {
                                    field.onChange([
                                      ...current,
                                      model.catalog_ref,
                                    ])
                                    return
                                  }
                                  field.onChange(
                                    current.filter(
                                      (value) => value !== model.catalog_ref
                                    )
                                  )
                                }}
                              />
                              <div className="min-w-0 space-y-1">
                                <div className="flex items-center gap-2 text-sm font-medium">
                                  <ProviderIcon
                                    className="size-4 rounded-sm"
                                    providerId={getProviderIconId(
                                      model.runtime_provider
                                    )}
                                  />
                                  <span>{model.display_name}</span>
                                </div>
                                <div className="text-xs text-muted-foreground">
                                  {model.runtime_provider}
                                  {" · "}
                                  {model.source_name}
                                </div>
                                <div className="text-xs text-muted-foreground">
                                  {model.model_name}
                                </div>
                              </div>
                            </label>
                          )
                        })
                      ) : (
                        <div className="px-4 py-6 text-sm text-muted-foreground">
                          No organization-enabled models matched the current
                          search.
                        </div>
                      )}
                    </div>
                  </ScrollArea>
                  <p className="text-xs text-muted-foreground">
                    {inheritOrgModels
                      ? "This workspace currently inherits every organization-enabled model."
                      : `${field.value?.length ?? 0} models selected for this workspace.`}
                  </p>
                </div>
              ) : (
                <p className="text-sm text-muted-foreground">
                  No organization-enabled models are available yet.
                </p>
              )}
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
