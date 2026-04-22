"use client"

import { zodResolver } from "@hookform/resolvers/zod"
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query"
import { Loader2 } from "lucide-react"
import { useEffect, useMemo, useState } from "react"
import { useForm } from "react-hook-form"
import { z } from "zod"
import type {
  AgentCatalogRead,
  AgentCustomProviderRead,
  AgentModelAccessRead,
  ApiError,
  WorkspaceRead,
} from "@/client"
import {
  disableModel,
  enableModel,
  listCatalog,
  listCustomProviders,
  listEnabledModels,
} from "@/client"
import { ProviderIcon } from "@/components/icons"
import { Alert, AlertDescription, AlertTitle } from "@/components/ui/alert"
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
import { toast } from "@/components/ui/use-toast"
import { getApiErrorDetail, retryHandler } from "@/lib/errors"
import { cn } from "@/lib/utils"

const CURSOR_PAGE_SIZE = 100

const workspaceModelSettingsSchema = z
  .object({
    inherit_all: z.boolean().default(true),
    catalog_ids: z.array(z.string()).default([]),
  })
  .superRefine((values, ctx) => {
    if (values.inherit_all || values.catalog_ids.length > 0) {
      return
    }

    ctx.addIssue({
      code: z.ZodIssueCode.custom,
      message: "Select at least one model or inherit the organization catalog.",
      path: ["catalog_ids"],
    })
  })

type WorkspaceModelSettingsForm = z.infer<typeof workspaceModelSettingsSchema>

function getProviderIconId(provider?: string | null): string {
  switch (provider) {
    case "anthropic":
      return "anthropic"
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
    default:
      return "custom"
  }
}

async function fetchAllCatalogEntries(): Promise<AgentCatalogRead[]> {
  const items: AgentCatalogRead[] = []
  let cursor: string | undefined

  do {
    const response = await listCatalog({
      cursor,
      limit: CURSOR_PAGE_SIZE,
    })
    items.push(...response.items)
    cursor = response.next_cursor ?? undefined
  } while (cursor)

  return items
}

async function fetchAllEnabledAccessRows(): Promise<AgentModelAccessRead[]> {
  const items: AgentModelAccessRead[] = []
  let cursor: string | undefined

  do {
    const response = await listEnabledModels({
      cursor,
      limit: CURSOR_PAGE_SIZE,
    })
    items.push(...response.items)
    cursor = response.next_cursor ?? undefined
  } while (cursor)

  return items
}

async function fetchAllProviders(): Promise<AgentCustomProviderRead[]> {
  const items: AgentCustomProviderRead[] = []
  let cursor: string | undefined

  do {
    const response = await listCustomProviders({
      cursor,
      limit: CURSOR_PAGE_SIZE,
    })
    items.push(...response.items)
    cursor = response.next_cursor ?? undefined
  } while (cursor)

  return items
}

function getModelSourceLabel(
  entry: AgentCatalogRead,
  providersById: Map<string, AgentCustomProviderRead>
): string {
  if (entry.custom_provider_id) {
    return providersById.get(entry.custom_provider_id)?.display_name ?? "Custom"
  }
  return "Platform"
}

/**
 * Manage workspace AI model availability with the branch-style inherit/subset
 * UI, backed by the current catalog and access APIs.
 */
export function WorkspaceModelSettings({
  workspace,
}: {
  workspace: WorkspaceRead
}) {
  const queryClient = useQueryClient()
  const [modelQuery, setModelQuery] = useState("")
  const form = useForm<WorkspaceModelSettingsForm>({
    resolver: zodResolver(workspaceModelSettingsSchema),
    mode: "onChange",
    defaultValues: {
      inherit_all: true,
      catalog_ids: [],
    },
  })

  const {
    data: catalogEntries,
    isLoading: catalogEntriesLoading,
    error: catalogEntriesError,
  } = useQuery<AgentCatalogRead[], ApiError>({
    queryKey: ["organization", "agent-catalog"],
    queryFn: fetchAllCatalogEntries,
    retry: retryHandler,
  })

  const {
    data: enabledAccessRows,
    isLoading: enabledAccessRowsLoading,
    error: enabledAccessRowsError,
  } = useQuery<AgentModelAccessRead[], ApiError>({
    queryKey: ["organization", "agent-model-access"],
    queryFn: fetchAllEnabledAccessRows,
    retry: retryHandler,
  })

  const {
    data: customProviders,
    isLoading: customProvidersLoading,
    error: customProvidersError,
  } = useQuery<AgentCustomProviderRead[], ApiError>({
    queryKey: ["organization", "agent-providers"],
    queryFn: fetchAllProviders,
    retry: retryHandler,
  })

  const providersById = useMemo(
    () =>
      new Map(
        (customProviders ?? []).map((provider) => [provider.id, provider])
      ),
    [customProviders]
  )
  const orgEnabledAccessRows = useMemo(
    () => (enabledAccessRows ?? []).filter((row) => row.workspace_id === null),
    [enabledAccessRows]
  )
  const workspaceAccessRows = useMemo(
    () =>
      (enabledAccessRows ?? []).filter(
        (row) => row.workspace_id === workspace.id
      ),
    [enabledAccessRows, workspace.id]
  )
  const orgEnabledCatalogIds = useMemo(
    () => new Set(orgEnabledAccessRows.map((row) => row.catalog_id)),
    [orgEnabledAccessRows]
  )
  const workspaceAccessByCatalogId = useMemo(
    () => new Map(workspaceAccessRows.map((row) => [row.catalog_id, row])),
    [workspaceAccessRows]
  )
  const organizationEnabledEntries = useMemo(() => {
    const entries = (catalogEntries ?? []).filter((entry) =>
      orgEnabledCatalogIds.has(entry.id)
    )

    return entries.sort((left, right) => {
      const leftSource = getModelSourceLabel(left, providersById)
      const rightSource = getModelSourceLabel(right, providersById)
      const sourceComparison = leftSource.localeCompare(rightSource)
      if (sourceComparison !== 0) {
        return sourceComparison
      }
      const providerComparison = left.model_provider.localeCompare(
        right.model_provider
      )
      if (providerComparison !== 0) {
        return providerComparison
      }
      return left.model_name.localeCompare(right.model_name)
    })
  }, [catalogEntries, orgEnabledCatalogIds, providersById])

  useEffect(() => {
    if (!catalogEntries || !enabledAccessRows) {
      return
    }

    const selectedCatalogIds = organizationEnabledEntries
      .filter((entry) => workspaceAccessByCatalogId.has(entry.id))
      .map((entry) => entry.id)
    const hasWorkspaceOverride = workspaceAccessRows.length > 0
    const matchesAllOrganizationModels =
      organizationEnabledEntries.every((entry) =>
        workspaceAccessByCatalogId.has(entry.id)
      ) &&
      workspaceAccessRows.every((row) =>
        orgEnabledCatalogIds.has(row.catalog_id)
      )

    form.reset({
      inherit_all: !hasWorkspaceOverride || matchesAllOrganizationModels,
      catalog_ids: selectedCatalogIds,
    })
  }, [
    catalogEntries,
    enabledAccessRows,
    form,
    organizationEnabledEntries,
    orgEnabledCatalogIds,
    workspaceAccessByCatalogId,
    workspaceAccessRows,
  ])

  const inheritAll = form.watch("inherit_all")
  const selectedCatalogIds = form.watch("catalog_ids")
  const selectedCatalogIdSet = useMemo(
    () => new Set(selectedCatalogIds ?? []),
    [selectedCatalogIds]
  )
  const filteredEntries = useMemo(() => {
    const query = modelQuery.trim().toLowerCase()
    if (!query) {
      return organizationEnabledEntries
    }

    return organizationEnabledEntries.filter((entry) => {
      const sourceLabel = getModelSourceLabel(
        entry,
        providersById
      ).toLowerCase()
      return (
        entry.model_name.toLowerCase().includes(query) ||
        entry.model_provider.toLowerCase().includes(query) ||
        sourceLabel.includes(query)
      )
    })
  }, [modelQuery, organizationEnabledEntries, providersById])

  const updateWorkspaceModelSubset = useMutation({
    mutationFn: async (values: WorkspaceModelSettingsForm) => {
      const desiredCatalogIds = new Set(values.catalog_ids)
      const accessRowsToDisable = values.inherit_all
        ? workspaceAccessRows
        : workspaceAccessRows.filter(
            (row) =>
              !desiredCatalogIds.has(row.catalog_id) ||
              !orgEnabledCatalogIds.has(row.catalog_id)
          )
      const catalogIdsToEnable = values.inherit_all
        ? []
        : [...desiredCatalogIds].filter(
            (catalogId) => !workspaceAccessByCatalogId.has(catalogId)
          )

      for (const row of accessRowsToDisable) {
        await disableModel({ accessId: row.id })
      }
      for (const catalogId of catalogIdsToEnable) {
        await enableModel({
          requestBody: {
            catalog_id: catalogId,
            workspace_id: workspace.id,
          },
        })
      }
    },
    onSuccess: () => {
      void queryClient.invalidateQueries({
        queryKey: ["organization", "agent-model-access"],
      })
      void queryClient.invalidateQueries({
        queryKey: ["workspace", workspace.id, "agent-model-access"],
      })
      toast({
        title: "Workspace models updated",
        description: `Saved the AI model settings for ${workspace.name}.`,
      })
    },
    onError: (error: ApiError) => {
      toast({
        title: "Save failed",
        description:
          getApiErrorDetail(error) ??
          "Unable to update workspace AI model access.",
        variant: "destructive",
      })
    },
  })

  async function onSubmit(values: WorkspaceModelSettingsForm) {
    await updateWorkspaceModelSubset.mutateAsync(values)
  }

  if (
    catalogEntriesLoading ||
    enabledAccessRowsLoading ||
    customProvidersLoading
  ) {
    return (
      <div className="flex items-center justify-center py-10">
        <Loader2 className="size-5 animate-spin text-muted-foreground" />
      </div>
    )
  }

  if (catalogEntriesError || enabledAccessRowsError || customProvidersError) {
    return (
      <Alert variant="destructive">
        <AlertTitle>Unable to load workspace model access</AlertTitle>
        <AlertDescription>
          {getApiErrorDetail(
            catalogEntriesError ??
              enabledAccessRowsError ??
              customProvidersError
          ) ?? "Something went wrong."}
        </AlertDescription>
      </Alert>
    )
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
          name="catalog_ids"
          render={({ field, fieldState }) => (
            <FormItem className="space-y-3 rounded-lg border p-4">
              <div className="space-y-0.5">
                <FormLabel>Workspace model subset</FormLabel>
                <FormDescription>
                  Choose which organization-enabled models remain available in
                  this workspace.
                </FormDescription>
              </div>

              {organizationEnabledEntries.length ? (
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
                      {filteredEntries.length ? (
                        filteredEntries.map((entry) => {
                          const checked = inheritAll
                            ? true
                            : selectedCatalogIdSet.has(entry.id)
                          const sourceName = getModelSourceLabel(
                            entry,
                            providersById
                          )

                          return (
                            <label
                              key={entry.id}
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
                                    field.onChange([
                                      ...new Set([...current, entry.id]),
                                    ])
                                    return
                                  }
                                  field.onChange(
                                    current.filter(
                                      (value) => value !== entry.id
                                    )
                                  )
                                }}
                              />
                              <div className="min-w-0 space-y-1">
                                <div className="flex items-center gap-2 text-sm font-medium">
                                  <ProviderIcon
                                    className="size-4 rounded-sm"
                                    providerId={getProviderIconId(
                                      entry.model_provider
                                    )}
                                  />
                                  <span>{entry.model_name}</span>
                                </div>
                                <div className="text-xs text-muted-foreground">
                                  {entry.model_provider}
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

        <Button
          type="submit"
          disabled={updateWorkspaceModelSubset.isPending}
          size="sm"
        >
          {updateWorkspaceModelSubset.isPending ? "Saving..." : "Save"}
        </Button>
      </form>
    </Form>
  )
}
