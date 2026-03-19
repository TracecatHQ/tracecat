"use client"

import { zodResolver } from "@hookform/resolvers/zod"
import { useEffect, useMemo, useState } from "react"
import { useForm } from "react-hook-form"
import { z } from "zod"
import type { ModelSelection, WorkspaceRead } from "@/client"
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
import {
  type AgentCatalogEntry,
  getModelSelectionKey,
  useAgentModels,
  useWorkspaceAgentModelSubset,
} from "@/lib/hooks"
import { cn } from "@/lib/utils"

const modelSelectionSchema = z.object({
  source_id: z.string().nullish(),
  model_provider: z.string().min(1),
  model_name: z.string().min(1),
})

export const workspaceModelSettingsSchema = z
  .object({
    inherit_all: z.boolean().default(true),
    models: z.array(modelSelectionSchema).default([]),
  })
  .superRefine((values, ctx) => {
    if (values.inherit_all || values.models.length > 0) {
      return
    }

    ctx.addIssue({
      code: z.ZodIssueCode.custom,
      message: "Select at least one model or inherit the organization catalog.",
      path: ["models"],
    })
  })

type WorkspaceModelSettingsForm = z.infer<typeof workspaceModelSettingsSchema>

function toModelSelection(
  model: Pick<AgentCatalogEntry, "source_id" | "model_provider" | "model_name">
): ModelSelection {
  return {
    source_id: model.source_id ?? null,
    model_provider: model.model_provider,
    model_name: model.model_name,
  }
}

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
  return values.inherit_all ? null : values.models
}

interface WorkspaceModelSettingsProps {
  workspace: WorkspaceRead
}

export function WorkspaceModelSettings({
  workspace,
}: WorkspaceModelSettingsProps) {
  const [modelQuery, setModelQuery] = useState("")
  const { models, modelsLoading } = useAgentModels()
  const {
    modelSubset,
    modelSubsetLoading,
    updateModelSubset,
    isUpdatingModelSubset,
  } = useWorkspaceAgentModelSubset(workspace.id)

  const form = useForm<WorkspaceModelSettingsForm>({
    resolver: zodResolver(workspaceModelSettingsSchema),
    mode: "onChange",
    defaultValues: {
      inherit_all: true,
      models: [],
    },
  })

  useEffect(() => {
    if (modelSubsetLoading) {
      return
    }

    form.reset({
      inherit_all: modelSubset === null,
      models: modelSubset ?? [],
    })
  }, [form, modelSubset, modelSubsetLoading])

  const inheritAll = form.watch("inherit_all")
  const selectedModels = form.watch("models")
  const selectedModelKeys = useMemo(
    () =>
      new Set(
        (selectedModels ?? []).map((model) => getModelSelectionKey(model))
      ),
    [selectedModels]
  )
  const filteredModels = useMemo(() => {
    const query = modelQuery.trim().toLowerCase()
    if (!query) {
      return models ?? []
    }
    return (
      models?.filter((model) => {
        return (
          model.model_name.toLowerCase().includes(query) ||
          model.model_provider.toLowerCase().includes(query) ||
          (model.source_name ?? "").toLowerCase().includes(query)
        )
      }) ?? []
    )
  }, [modelQuery, models])

  async function onSubmit(values: WorkspaceModelSettingsForm) {
    await updateModelSubset(buildWorkspaceModelSettingsUpdate(values))
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
          name="inherit_all"
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
          name="models"
          render={({ field, fieldState }) => (
            <FormItem className="space-y-3 rounded-lg border p-4">
              <div className="space-y-0.5">
                <FormLabel>Workspace model subset</FormLabel>
                <FormDescription>
                  Choose which organization-enabled models remain available in
                  this workspace.
                </FormDescription>
              </div>

              {modelsLoading || modelSubsetLoading ? (
                <p className="text-sm text-muted-foreground">
                  Loading enabled models...
                </p>
              ) : models?.length ? (
                <div className="space-y-3">
                  <Input
                    disabled={inheritAll}
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
                          const modelSelection = toModelSelection(model)
                          const modelKey = getModelSelectionKey(modelSelection)
                          const checked = inheritAll
                            ? true
                            : selectedModelKeys.has(modelKey)
                          const sourceName =
                            model.source_name ??
                            (model.source_id ? "Custom" : "Platform")

                          return (
                            <label
                              key={modelKey}
                              className={cn(
                                "flex items-start gap-3 px-4 py-3 transition-colors",
                                inheritAll
                                  ? "cursor-not-allowed opacity-60"
                                  : "cursor-pointer hover:bg-muted/30"
                              )}
                            >
                              <Checkbox
                                checked={checked}
                                disabled={inheritAll}
                                onCheckedChange={(nextChecked) => {
                                  const current = field.value ?? []
                                  if (nextChecked) {
                                    field.onChange([...current, modelSelection])
                                    return
                                  }
                                  field.onChange(
                                    current.filter(
                                      (value) =>
                                        getModelSelectionKey(value) !== modelKey
                                    )
                                  )
                                }}
                              />
                              <div className="min-w-0 space-y-1">
                                <div className="flex items-center gap-2 text-sm font-medium">
                                  <ProviderIcon
                                    className="size-4 rounded-sm"
                                    providerId={getProviderIconId(
                                      model.model_provider
                                    )}
                                  />
                                  <span>{model.model_name}</span>
                                </div>
                                <div className="text-xs text-muted-foreground">
                                  {model.model_provider}
                                  {" · "}
                                  {sourceName}
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
                  {fieldState.error ? (
                    <p className="text-sm text-destructive">
                      {fieldState.error.message}
                    </p>
                  ) : null}
                  <p className="text-xs text-muted-foreground">
                    {inheritAll
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

        <Button type="submit" disabled={isUpdatingModelSubset} size="sm">
          {isUpdatingModelSubset ? "Saving..." : "Save"}
        </Button>
      </form>
    </Form>
  )
}
