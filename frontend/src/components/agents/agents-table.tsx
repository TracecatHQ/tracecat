"use client"

import { DotsHorizontalIcon } from "@radix-ui/react-icons"
import { useQuery } from "@tanstack/react-query"
import type { ColumnDef } from "@tanstack/react-table"
import { format } from "date-fns"
import { Copy, CopyPlus, Trash2 } from "lucide-react"
import { useRouter } from "next/navigation"
import { useCallback, useMemo, useRef, useState } from "react"
import type { AgentPresetRead, AgentPresetReadMinimal } from "@/client"
import {
  agentPresetsGetAgentPreset,
  agentPresetsListAgentPresets,
} from "@/client"
import { AgentPresetDeleteDialog } from "@/components/agents/agent-preset-delete-dialog"
import {
  DataTable,
  DataTableColumnHeader,
  type DataTableToolbarProps,
} from "@/components/data-table"
import { getIcon, ProviderIcon } from "@/components/icons"
import { Badge } from "@/components/ui/badge"
import { Button } from "@/components/ui/button"
import {
  DropdownMenu,
  DropdownMenuContent,
  DropdownMenuItem,
  DropdownMenuSeparator,
  DropdownMenuTrigger,
} from "@/components/ui/dropdown-menu"
import {
  Tooltip,
  TooltipContent,
  TooltipProvider,
  TooltipTrigger,
} from "@/components/ui/tooltip"
import { toast } from "@/components/ui/use-toast"
import {
  useCreateAgentPreset,
  useDeleteAgentPreset,
} from "@/hooks/use-agent-presets"
import { useAuth } from "@/hooks/use-auth"
import { buildDuplicateAgentPresetPayload } from "@/lib/agent-presets"
import { retryHandler, type TracecatApiError } from "@/lib/errors"
import { useListMcpIntegrations } from "@/lib/hooks"
import {
  capitalizeFirst,
  reconstructActionType,
  shortTimeAgo,
} from "@/lib/utils"
import { useWorkspaceId } from "@/providers/workspace-id"

type AgentPresetTableRow = AgentPresetReadMinimal & Partial<AgentPresetRead>

const toolPrefixes = {
  namespace: "namespace:",
}

function getMcpProviderId(slug: string): string | undefined {
  const slugMap: Record<string, string> = {
    "github-copilot": "github_mcp",
    github: "github_mcp",
    sentry: "sentry_mcp",
    notion: "notion_mcp",
    linear: "linear_mcp",
    jira: "jira_mcp",
    runreveal: "runreveal_mcp",
    "secure-annex": "secureannex_mcp",
    secureannex: "secureannex_mcp",
    wiz: "wiz_mcp",
  }

  const normalized = slug.toLowerCase()
  if (slugMap[normalized]) {
    return slugMap[normalized]
  }

  // Normalize "<name>[-_]mcp" into "<normalized_name>_mcp".
  // Examples:
  // - github_mcp -> github_mcp
  // - secure_annex_mcp -> secureannex_mcp
  // - secure-annex-mcp -> secureannex_mcp
  const mcpMatch = normalized.match(/^(.*?)(?:[_-]?mcp)$/)
  if (mcpMatch && mcpMatch[1]) {
    const compactBase = mcpMatch[1].replace(/[^a-z0-9]/g, "")
    if (compactBase) {
      return `${compactBase}_mcp`
    }
  }

  if (normalized.endsWith("_mcp")) {
    return normalized
  }

  if (normalized.endsWith("-mcp")) {
    return normalized.replace(/-/g, "_")
  }

  return undefined
}

type ToolBadge = {
  id: string
  label: string
  iconKey?: string
  providerId?: string
}

const normalizeToolName = (tool: string) => reconstructActionType(tool)

type McpIntegrationMeta = {
  name: string
  slug: string
}

const buildToolBadges = (preset: AgentPresetTableRow) => {
  const tools: ToolBadge[] = []
  const seen = new Set<string>()

  const addTool = (tool: ToolBadge) => {
    if (seen.has(tool.id)) {
      return
    }
    seen.add(tool.id)
    tools.push(tool)
  }

  if (preset.actions?.length) {
    preset.actions.forEach((action) => {
      const normalized = normalizeToolName(action)
      addTool({
        id: `action:${normalized}`,
        label: normalized,
        iconKey: normalized,
      })
    })
  }

  if (preset.namespaces?.length) {
    preset.namespaces.forEach((namespace) => {
      const normalized = normalizeToolName(namespace)
      addTool({
        id: `${toolPrefixes.namespace}${normalized}`,
        label: `${toolPrefixes.namespace}${normalized}`,
        iconKey: normalized,
      })
    })
  }

  return tools
}

const buildMcpBadges = (
  preset: AgentPresetTableRow,
  mcpIntegrationMap: Map<string, McpIntegrationMeta>
) => {
  const tools: ToolBadge[] = []
  const seen = new Set<string>()

  const addTool = (tool: ToolBadge) => {
    if (seen.has(tool.id)) {
      return
    }
    seen.add(tool.id)
    tools.push(tool)
  }

  if (preset.mcp_integrations?.length) {
    preset.mcp_integrations.forEach((integration) => {
      const meta = mcpIntegrationMap.get(integration)
      const name = meta?.name
      const providerId = meta?.slug
        ? (getMcpProviderId(meta.slug) ?? "custom")
        : "custom"
      addTool({
        id: integration,
        label: name ?? integration,
        providerId,
      })
    })
  }

  return tools
}

const hasToolsConfig = (preset: AgentPresetTableRow) =>
  preset.actions !== undefined || preset.namespaces !== undefined

function toDuplicateSourcePreset(
  preset: AgentPresetTableRow
): AgentPresetRead | null {
  if (
    typeof preset.slug !== "string" ||
    typeof preset.model_name !== "string" ||
    typeof preset.model_provider !== "string"
  ) {
    return null
  }

  return {
    id: preset.id,
    workspace_id: preset.workspace_id,
    name: preset.name,
    slug: preset.slug,
    description: preset.description ?? null,
    current_version_id: preset.current_version_id ?? null,
    created_at: preset.created_at,
    updated_at: preset.updated_at,
    instructions: preset.instructions ?? null,
    model_name: preset.model_name,
    model_provider: preset.model_provider,
    base_url: preset.base_url ?? null,
    output_type: preset.output_type ?? null,
    actions: preset.actions ?? null,
    namespaces: preset.namespaces ?? null,
    tool_approvals: preset.tool_approvals ?? null,
    mcp_integrations: preset.mcp_integrations ?? null,
    retries: preset.retries,
    enable_internet_access: preset.enable_internet_access,
  }
}

const renderRelativeDate = (value?: string) => {
  if (!value) {
    return <span className="text-xs text-muted-foreground">-</span>
  }

  const dt = new Date(value)
  const shortTime = capitalizeFirst(shortTimeAgo(dt))
  const fullDateTime = format(dt, "PPpp")

  return (
    <Tooltip>
      <TooltipTrigger asChild>
        <span className="flex w-full justify-end">
          <span className="block truncate text-right text-xs">{shortTime}</span>
        </span>
      </TooltipTrigger>
      <TooltipContent>
        <p>{fullDateTime}</p>
      </TooltipContent>
    </Tooltip>
  )
}

function AgentActionsMenu({
  preset,
  onCopy,
  onDuplicate,
  onDelete,
  isDuplicating,
}: {
  preset: AgentPresetTableRow
  onCopy: (preset: AgentPresetTableRow) => void
  onDuplicate: (preset: AgentPresetTableRow) => void
  onDelete: (preset: AgentPresetTableRow) => void
  isDuplicating: boolean
}) {
  return (
    <DropdownMenu>
      <DropdownMenuTrigger asChild>
        <Button
          variant="ghost"
          className="size-6 p-0"
          onClick={(event) => event.stopPropagation()}
        >
          <span className="sr-only">Open menu</span>
          <DotsHorizontalIcon className="size-4" />
        </Button>
      </DropdownMenuTrigger>
      <DropdownMenuContent
        align="end"
        className="min-w-0 w-fit"
        onClick={(event) => event.stopPropagation()}
      >
        <DropdownMenuItem className="text-xs" onClick={() => onCopy(preset)}>
          <Copy className="mr-2 size-3.5" />
          Copy agent ID
        </DropdownMenuItem>
        <DropdownMenuItem
          className="text-xs"
          disabled={isDuplicating}
          onClick={() => onDuplicate(preset)}
        >
          <CopyPlus className="mr-2 size-3.5" />
          {isDuplicating ? "Duplicating..." : "Duplicate agent"}
        </DropdownMenuItem>
        <DropdownMenuSeparator />
        <DropdownMenuItem
          className="text-xs text-rose-500 focus:text-rose-600"
          onClick={() => onDelete(preset)}
        >
          <Trash2 className="mr-2 size-3.5" />
          Delete agent
        </DropdownMenuItem>
      </DropdownMenuContent>
    </DropdownMenu>
  )
}

export function AgentsTable() {
  const router = useRouter()
  const workspaceId = useWorkspaceId()
  const { user } = useAuth()
  const [selectedPreset, setSelectedPreset] =
    useState<AgentPresetTableRow | null>(null)
  const [deleteDialogOpen, setDeleteDialogOpen] = useState(false)
  const [duplicatingPresetId, setDuplicatingPresetId] = useState<string | null>(
    null
  )
  const duplicatingPresetIdRef = useRef<string | null>(null)
  const { createAgentPreset } = useCreateAgentPreset(workspaceId)
  const { deleteAgentPreset, deleteAgentPresetIsPending } =
    useDeleteAgentPreset(workspaceId)
  const { mcpIntegrations } = useListMcpIntegrations(workspaceId)

  const {
    data: presets,
    isLoading: presetsIsLoading,
    error: presetsError,
    refetch: refetchPresets,
  } = useQuery<AgentPresetTableRow[], TracecatApiError>({
    queryKey: ["agent-presets-detailed", workspaceId],
    queryFn: async () => {
      if (!workspaceId) {
        throw new Error("workspaceId is required to list agent presets")
      }
      const minimalPresets = await agentPresetsListAgentPresets({
        workspaceId,
      })

      if (!minimalPresets.length) {
        return []
      }

      const detailedResults = await Promise.allSettled(
        minimalPresets.map((preset) =>
          agentPresetsGetAgentPreset({
            workspaceId,
            presetId: preset.id,
          })
        )
      )

      return minimalPresets.map((preset, index) => {
        const result = detailedResults[index]
        if (result.status === "fulfilled") {
          return { ...preset, ...result.value }
        }
        return preset
      })
    },
    enabled: Boolean(workspaceId),
    retry: retryHandler,
  })

  const mcpIntegrationMap = useMemo(
    () =>
      new Map(
        (mcpIntegrations ?? []).map((integration) => [
          integration.id,
          { name: integration.name, slug: integration.slug },
        ])
      ),
    [mcpIntegrations]
  )

  const toolbarProps: DataTableToolbarProps<AgentPresetTableRow> = useMemo(
    () => ({
      filterProps: {
        placeholder: "Search agents...",
        column: "name",
      },
    }),
    []
  )

  const handleCopy = useCallback(async (preset: AgentPresetTableRow) => {
    try {
      await navigator.clipboard.writeText(preset.id)
      toast({
        title: "Agent ID copied",
        description: (
          <div className="flex flex-col space-y-2">
            <span>
              Agent ID copied for <b className="inline-block">{preset.name}</b>
            </span>
            <span className="text-muted-foreground">ID: {preset.id}</span>
          </div>
        ),
      })
    } catch (error) {
      console.error("Failed to copy to clipboard:", error)
      toast({
        title: "Failed to copy",
        description: "Could not copy agent ID to clipboard",
        variant: "destructive",
      })
    }
  }, [])

  const handleDeleteRequest = useCallback((preset: AgentPresetTableRow) => {
    setSelectedPreset(preset)
    setDeleteDialogOpen(true)
  }, [])

  const handleDuplicate = useCallback(
    async (preset: AgentPresetTableRow) => {
      if (duplicatingPresetIdRef.current === preset.id) {
        return
      }

      duplicatingPresetIdRef.current = preset.id
      setDuplicatingPresetId(preset.id)
      try {
        const sourcePreset = toDuplicateSourcePreset(preset)
        const fullPreset =
          sourcePreset ??
          (await agentPresetsGetAgentPreset({
            workspaceId,
            presetId: preset.id,
          }))
        const existingSlugs =
          presets
            ?.map((item) => item.slug)
            .filter((slug): slug is string => typeof slug === "string") ?? []
        const createdPreset = await createAgentPreset(
          buildDuplicateAgentPresetPayload(fullPreset, existingSlugs)
        )
        router.push(`/workspaces/${workspaceId}/agents/${createdPreset.id}`)
      } catch (error) {
        console.error("Failed to duplicate agent preset:", error)
        toast({
          title: "Duplicate failed",
          description: "Could not duplicate agent. Please try again.",
          variant: "destructive",
        })
      } finally {
        duplicatingPresetIdRef.current = null
        setDuplicatingPresetId(null)
      }
    },
    [createAgentPreset, presets, router, workspaceId]
  )

  const handleDeleteConfirm = useCallback(async () => {
    if (!selectedPreset) {
      return
    }

    try {
      await deleteAgentPreset({
        presetId: selectedPreset.id,
        presetName: selectedPreset.name,
      })
      setDeleteDialogOpen(false)
      setSelectedPreset(null)
      void refetchPresets()
    } catch (error) {
      console.error("Failed to delete agent preset:", error)
    }
  }, [deleteAgentPreset, refetchPresets, selectedPreset])

  const columns = useMemo<ColumnDef<AgentPresetTableRow>[]>(
    () => [
      {
        accessorKey: "name",
        header: ({ column }) => (
          <DataTableColumnHeader column={column} title="Agent" />
        ),
        cell: ({ row }) => {
          const name = row.getValue<AgentPresetTableRow["name"]>("name")
          return (
            <Tooltip>
              <TooltipTrigger asChild>
                <span className="block truncate text-xs font-medium">
                  {name || "Untitled agent"}
                </span>
              </TooltipTrigger>
              {name ? (
                <TooltipContent className="max-w-sm break-words text-xs">
                  <p>{name}</p>
                </TooltipContent>
              ) : null}
            </Tooltip>
          )
        },
        meta: {
          headerClassName: "w-[220px] min-w-[220px] max-w-[220px] text-left",
          cellClassName: "w-[220px] min-w-[220px] max-w-[220px] text-left",
          headerStyle: { width: "220px" },
          cellStyle: { width: "220px" },
        },
      },
      {
        id: "tools",
        header: ({ column }) => (
          <DataTableColumnHeader column={column} title="Tools" />
        ),
        enableSorting: false,
        cell: ({ row }) => {
          const toolBadges = buildToolBadges(row.original)
          const showTools = hasToolsConfig(row.original)

          if (!showTools) {
            return <span className="text-xs text-muted-foreground">-</span>
          }

          const toolList = toolBadges.length
            ? toolBadges
            : [{ id: "all-tools", label: "All tools" }]

          return (
            <div className="flex flex-wrap items-center gap-1 text-xs">
              {toolList.map((tool) => (
                <Badge
                  key={tool.id}
                  variant="secondary"
                  className="font-medium"
                >
                  <span className="flex items-center gap-1.5">
                    {tool.iconKey ? (
                      getIcon(tool.iconKey, {
                        className: "size-5 shrink-0",
                      })
                    ) : tool.providerId ? (
                      <ProviderIcon
                        providerId={tool.providerId ?? "custom"}
                        className="size-5 shrink-0"
                      />
                    ) : null}
                    <span>{tool.label}</span>
                  </span>
                </Badge>
              ))}
            </div>
          )
        },
        meta: {
          headerClassName: "min-w-[260px] max-w-[40rem] text-left",
          cellClassName: "min-w-[260px] max-w-[40rem] text-left",
        },
      },
      {
        id: "mcp",
        header: ({ column }) => (
          <DataTableColumnHeader column={column} title="MCP" />
        ),
        enableSorting: false,
        cell: ({ row }) => {
          const mcpBadges = buildMcpBadges(row.original, mcpIntegrationMap)

          if (mcpBadges.length === 0) {
            return <span className="text-xs text-muted-foreground">-</span>
          }

          return (
            <div className="flex flex-wrap items-center gap-1 text-xs">
              {mcpBadges.map((tool) => (
                <Badge
                  key={tool.id}
                  variant="secondary"
                  className="font-medium"
                >
                  <span className="flex items-center gap-1.5">
                    {tool.providerId ? (
                      <ProviderIcon
                        providerId={tool.providerId ?? "custom"}
                        className="size-5 shrink-0"
                      />
                    ) : null}
                    <span>{tool.label}</span>
                  </span>
                </Badge>
              ))}
            </div>
          )
        },
        meta: {
          headerClassName: "min-w-[200px] max-w-[24rem] text-left",
          cellClassName: "min-w-[200px] max-w-[24rem] text-left",
        },
      },
      {
        accessorKey: "model_provider",
        header: ({ column }) => (
          <DataTableColumnHeader
            className="text-xs"
            column={column}
            title="Provider"
          />
        ),
        cell: ({ row }) => {
          const provider =
            row.getValue<AgentPresetTableRow["model_provider"]>(
              "model_provider"
            )

          if (!provider) {
            return <span className="text-xs text-muted-foreground">-</span>
          }

          return (
            <span className="block truncate text-xs text-foreground/80">
              {provider}
            </span>
          )
        },
        meta: {
          headerClassName: "w-[160px] min-w-[160px] max-w-[160px] text-left",
          cellClassName: "w-[160px] min-w-[160px] max-w-[160px] text-left",
          headerStyle: { width: "160px" },
          cellStyle: { width: "160px" },
        },
      },
      {
        accessorKey: "model_name",
        header: ({ column }) => (
          <DataTableColumnHeader
            className="text-xs"
            column={column}
            title="Model"
          />
        ),
        cell: ({ row }) => {
          const model =
            row.getValue<AgentPresetTableRow["model_name"]>("model_name")
          return model ? (
            <span className="block truncate text-xs text-foreground/80">
              {model}
            </span>
          ) : (
            <span className="text-xs text-muted-foreground">-</span>
          )
        },
        meta: {
          headerClassName: "w-[200px] min-w-[200px] max-w-[200px] text-left",
          cellClassName: "w-[200px] min-w-[200px] max-w-[200px] text-left",
          headerStyle: { width: "200px" },
          cellStyle: { width: "200px" },
        },
      },
      {
        accessorKey: "created_at",
        header: ({ column }) => (
          <DataTableColumnHeader
            column={column}
            title="Created"
            className="justify-end"
            buttonClassName="ml-auto h-8 justify-end px-0 data-[state=open]:bg-accent"
          />
        ),
        cell: ({ row }) =>
          renderRelativeDate(
            row.getValue<AgentPresetTableRow["created_at"]>("created_at")
          ),
        meta: {
          headerClassName:
            "w-[110px] min-w-[110px] max-w-[110px] justify-end px-0 text-right",
          cellClassName:
            "w-[110px] min-w-[110px] max-w-[110px] px-0 pl-2 text-right",
          headerStyle: { width: "110px" },
          cellStyle: { width: "110px" },
        },
      },
      {
        accessorKey: "updated_at",
        header: ({ column }) => (
          <DataTableColumnHeader
            column={column}
            title="Updated"
            className="justify-end"
            buttonClassName="ml-auto h-8 justify-end px-0 data-[state=open]:bg-accent"
          />
        ),
        cell: ({ row }) =>
          renderRelativeDate(
            row.getValue<AgentPresetTableRow["updated_at"]>("updated_at")
          ),
        meta: {
          headerClassName:
            "w-[110px] min-w-[110px] max-w-[110px] justify-end px-0 text-right",
          cellClassName:
            "w-[110px] min-w-[110px] max-w-[110px] px-0 pl-2 text-right",
          headerStyle: { width: "110px" },
          cellStyle: { width: "110px" },
        },
      },
      {
        id: "actions",
        enableHiding: false,
        enableSorting: false,
        cell: ({ row }) => (
          <AgentActionsMenu
            preset={row.original}
            onCopy={handleCopy}
            onDuplicate={handleDuplicate}
            onDelete={handleDeleteRequest}
            isDuplicating={duplicatingPresetId === row.original.id}
          />
        ),
      },
    ],
    [handleCopy, handleDeleteRequest, handleDuplicate, mcpIntegrationMap]
  )

  return (
    <>
      <TooltipProvider>
        <DataTable
          tableId={`${workspaceId}-${user?.id ?? "guest"}:agents-table`}
          data={presets}
          columns={columns}
          toolbarProps={toolbarProps}
          isLoading={presetsIsLoading}
          error={presetsError ?? null}
          emptyMessage="No agents found."
          errorMessage="Error loading agents."
          getRowHref={(row) =>
            `/workspaces/${workspaceId}/agents/${row.original.id}`
          }
        />
      </TooltipProvider>
      <AgentPresetDeleteDialog
        open={deleteDialogOpen}
        onOpenChange={(nextOpen) => {
          setDeleteDialogOpen(nextOpen)
          if (!nextOpen) {
            setSelectedPreset(null)
          }
        }}
        presetName={selectedPreset?.name ?? "the agent"}
        isDeleting={deleteAgentPresetIsPending}
        onConfirm={handleDeleteConfirm}
      />
    </>
  )
}
