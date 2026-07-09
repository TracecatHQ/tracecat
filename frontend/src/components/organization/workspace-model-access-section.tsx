"use client"

import { zodResolver } from "@hookform/resolvers/zod"
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query"
import { Loader2 } from "lucide-react"
import { useEffect, useMemo, useRef, useState } from "react"
import { useForm } from "react-hook-form"
import { z } from "zod"
import type {
  AgentCatalogRead,
  AgentCustomProviderRead,
  AgentModelAccessRead,
  ApiError,
  WorkspaceReadMinimal,
} from "@/client"
import {
  disableModel,
  enableModel,
  listCatalog,
  listCustomProviders,
  listEnabledModels,
  workspacesListWorkspaces,
} from "@/client"
import { useScopeCheck } from "@/components/auth/scope-guard"
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
import {
  Select,
  SelectContent,
  SelectItem,
  SelectTrigger,
  SelectValue,
} from "@/components/ui/select"
import { Switch } from "@/components/ui/switch"
import { toast } from "@/components/ui/use-toast"
import { getApiErrorDetail, retryHandler } from "@/lib/errors"
import { cn } from "@/lib/utils"

const CURSOR_PAGE_SIZE = 100
export const WORKSPACE_MODEL_ACCESS_REQUIRED_SCOPES = [
  "agent:read",
  "agent:create",
  "agent:delete",
  "org:workspace:read",
]

const workspaceModelAccessSchema = z
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

type WorkspaceModelAccessForm = z.infer<typeof workspaceModelAccessSchema>

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

function getProviderDisplayLabel(provider: string): string {
  switch (provider) {
    case "anthropic":
      return "Anthropic"
    case "azure_ai":
      return "Azure AI"
    case "azure_openai":
      return "Azure OpenAI"
    case "bedrock":
      return "AWS Bedrock"
    case "gemini":
      return "Google Gemini"
    case "openai":
      return "OpenAI"
    case "vertex_ai":
      return "Google Vertex AI"
    case "custom-model-provider":
      return "Custom"
    default:
      return provider
        .split(/[_\s-]+/)
        .filter(Boolean)
        .map((part) => part.charAt(0).toUpperCase() + part.slice(1))
        .join(" ")
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
  return getProviderDisplayLabel(entry.model_provider)
}

function compareWorkspaces(
  left: WorkspaceReadMinimal,
  right: WorkspaceReadMinimal
) {
  const nameComparison = left.name.localeCompare(right.name)
  if (nameComparison !== 0) {
    return nameComparison
  }
  return left.id.localeCompare(right.id)
}

/**
 * Select a workspace and manage its AI model subset from organization settings.
 */
export function WorkspaceModelAccessSection() {
  const [selectedWorkspaceId, setSelectedWorkspaceId] = useState<
    string | undefined
  >()
  const canManageWorkspaceModelAccess =
    useScopeCheck(undefined, WORKSPACE_MODEL_ACCESS_REQUIRED_SCOPES, {
      all: true,
    }) === true
  const {
    data: workspaces,
    error: workspacesError,
    isLoading: workspacesLoading,
  } = useQuery<WorkspaceReadMinimal[], ApiError>({
    queryKey: ["workspaces"],
    queryFn: async () => await workspacesListWorkspaces(),
    staleTime: 5 * 60 * 1000,
    retry: retryHandler,
    enabled: canManageWorkspaceModelAccess,
  })
  const orderedWorkspaces = useMemo(
    () => [...(workspaces ?? [])].sort(compareWorkspaces),
    [workspaces]
  )
  const selectedWorkspace = orderedWorkspaces.find(
    (workspace) => workspace.id === selectedWorkspaceId
  )

  useEffect(() => {
    if (!selectedWorkspaceId) {
      return
    }
    if (selectedWorkspace) {
      return
    }
    setSelectedWorkspaceId(undefined)
  }, [selectedWorkspace, selectedWorkspaceId])

  if (!canManageWorkspaceModelAccess) {
    return null
  }

  if (workspacesLoading) {
    return (
      <div className="flex items-center justify-center py-10">
        <Loader2 className="size-5 animate-spin text-muted-foreground" />
      </div>
    )
  }

  if (workspacesError) {
    return (
      <Alert variant="destructive">
        <AlertTitle>Unable to load workspaces</AlertTitle>
        <AlertDescription>
          {getApiErrorDetail(workspacesError) ?? "Something went wrong."}
        </AlertDescription>
      </Alert>
    )
  }

  if (!orderedWorkspaces.length) {
    return (
      <div className="rounded-md border border-dashed px-3 py-4 text-sm text-muted-foreground">
        No workspaces are available.
      </div>
    )
  }

  return (
    <div className="space-y-4">
      <Select
        onValueChange={setSelectedWorkspaceId}
        value={selectedWorkspaceId}
      >
        <SelectTrigger className="max-w-sm">
          <SelectValue placeholder="Select a workspace" />
        </SelectTrigger>
        <SelectContent>
          {orderedWorkspaces.map((workspace) => (
            <SelectItem key={workspace.id} value={workspace.id}>
              {workspace.name}
            </SelectItem>
          ))}
        </SelectContent>
      </Select>

      {selectedWorkspace ? (
        <WorkspaceModelAccessEditor
          key={selectedWorkspace.id}
          workspaceId={selectedWorkspace.id}
          workspaceName={selectedWorkspace.name}
        />
      ) : (
        <div className="rounded-md border border-dashed px-3 py-4 text-sm text-muted-foreground">
          Choose a workspace to manage its AI model access.
        </div>
      )}
    </div>
  )
}

interface WorkspaceModelAccessEditorProps {
  workspaceId: string
  workspaceName: string
}

/**
 * Manage workspace AI model availability with the branch-style inherit/subset
 * UI, backed by the current catalog and access APIs.
 */
function WorkspaceModelAccessEditor({
  workspaceId,
  workspaceName,
}: WorkspaceModelAccessEditorProps) {
  const queryClient = useQueryClient()
  const [modelQuery, setModelQuery] = useState("")
  const lastSyncedFormValuesRef = useRef<string | null>(null)
  const form = useForm<WorkspaceModelAccessForm>({
    resolver: zodResolver(workspaceModelAccessSchema),
    mode: "onChange",
    defaultValues: {
      inherit_all: true,
      catalog_ids: [],
    },
  })
  const isFormDirty = form.formState.isDirty

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
        (row) => row.workspace_id === workspaceId
      ),
    [enabledAccessRows, workspaceId]
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
    const nextValues = {
      inherit_all: !hasWorkspaceOverride || matchesAllOrganizationModels,
      catalog_ids: selectedCatalogIds,
    }
    const nextValuesSignature = JSON.stringify(nextValues)

    if (lastSyncedFormValuesRef.current === nextValuesSignature) {
      return
    }
    if (isFormDirty) {
      return
    }

    form.reset(nextValues)
    lastSyncedFormValuesRef.current = nextValuesSignature
  }, [
    catalogEntries,
    enabledAccessRows,
    form,
    isFormDirty,
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
    mutationFn: async (values: WorkspaceModelAccessForm) => {
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
            workspace_id: workspaceId,
          },
        })
      }
    },
    onSuccess: (_data, values) => {
      form.reset(values)
      void queryClient.invalidateQueries({
        queryKey: ["organization", "agent-model-access"],
      })
      void queryClient.invalidateQueries({
        queryKey: ["workspace", workspaceId, "agent-models"],
      })
      toast({
        title: "Workspace models updated",
        description: `Saved the AI model settings for ${workspaceName}.`,
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

  async function onSubmit(values: WorkspaceModelAccessForm) {
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
