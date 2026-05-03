"use client"

import { zodResolver } from "@hookform/resolvers/zod"
import {
  AlertCircle,
  Bot,
  Box,
  Braces,
  Brackets,
  Check,
  ChevronsUpDown,
  CopyPlus,
  Hash,
  History,
  List,
  ListOrdered,
  ListTodo,
  Loader2,
  MessageCircle,
  MoreVertical,
  Percent,
  Plus,
  Pyramid,
  Save,
  SlidersHorizontal,
  Sparkles,
  ToggleLeft,
  Trash2,
  Type,
  Webhook,
} from "lucide-react"
import Link from "next/link"
import { useRouter } from "next/navigation"
import { useCallback, useEffect, useMemo, useRef, useState } from "react"
import {
  type FieldErrors,
  type UseFormReturn,
  useFieldArray,
  useForm,
} from "react-hook-form"
import { z } from "zod"
import type {
  AgentCatalogRead,
  AgentCustomProviderRead,
  AgentPresetCreate,
  AgentPresetRead,
  AgentPresetReadMinimal,
  AgentPresetUpdate,
  AttachedSubagentRef,
  SkillReadMinimal,
  SkillVersionRead,
} from "@/client"
import { AgentPresetDeleteDialog } from "@/components/agents/agent-preset-delete-dialog"
import { AgentPresetVersionSelect } from "@/components/agents/agent-preset-version-select"
import { AgentPresetVersionsPanel } from "@/components/agents/agent-preset-versions-panel"
import { SlackChannelPanel } from "@/components/agents/external-channels/slack-channel-panel"
import { ActionSelect } from "@/components/chat/action-select"
import { ChatHistoryDropdown } from "@/components/chat/chat-history-dropdown"
import { ChatSessionPane } from "@/components/chat/chat-session-pane"
import { CodeEditor } from "@/components/editor/codemirror/code-editor"
import { getIcon, ProviderIcon } from "@/components/icons"
import { CenteredSpinner } from "@/components/loading/spinner"
import { MultiTagCommandInput, type Suggestion } from "@/components/tags-input"
import { SimpleEditor } from "@/components/tiptap-templates/simple/simple-editor"
import { Alert, AlertDescription, AlertTitle } from "@/components/ui/alert"
import { Button } from "@/components/ui/button"
import {
  Command,
  CommandDialog,
  CommandEmpty,
  CommandGroup,
  CommandInput,
  CommandItem,
  CommandList,
} from "@/components/ui/command"
import {
  DropdownMenu,
  DropdownMenuContent,
  DropdownMenuItem,
  DropdownMenuSeparator,
  DropdownMenuTrigger,
} from "@/components/ui/dropdown-menu"
import {
  Empty,
  EmptyDescription,
  EmptyHeader,
  EmptyMedia,
  EmptyTitle,
} from "@/components/ui/empty"
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
import {
  Popover,
  PopoverContent,
  PopoverTrigger,
} from "@/components/ui/popover"
import {
  ResizableHandle,
  ResizablePanel,
  ResizablePanelGroup,
} from "@/components/ui/resizable"
import { ScrollArea } from "@/components/ui/scroll-area"
import {
  Select,
  SelectContent,
  SelectItem,
  SelectTrigger,
  SelectValue,
} from "@/components/ui/select"
import { Separator } from "@/components/ui/separator"
import { Switch } from "@/components/ui/switch"
import { Tabs, TabsContent, TabsList, TabsTrigger } from "@/components/ui/tabs"
import { Textarea } from "@/components/ui/textarea"
import {
  Tooltip,
  TooltipContent,
  TooltipTrigger,
} from "@/components/ui/tooltip"
import { toast } from "@/components/ui/use-toast"
import {
  useAgentPreset,
  useAgentPresets,
  useAgentPresetVersion,
  useAgentPresetVersions,
  useCreateAgentPreset,
  useDeleteAgentPreset,
  useUpdateAgentPreset,
} from "@/hooks/use-agent-presets"
import {
  useCreateChat,
  useGetChatVercel,
  useListChats,
  useUpdateChat,
} from "@/hooks/use-chat"
import { useEntitlements } from "@/hooks/use-entitlements"
import { useFeatureFlag } from "@/hooks/use-feature-flags"
import { useSkills, useSkillVersions } from "@/hooks/use-skills"
import {
  type AgentPresetFormMode,
  buildDuplicateAgentPresetPayload,
  getSubagentPresetUnavailableReason,
  getUnavailableSubagentPresetSlugs,
  SUBAGENT_APPROVAL_UNAVAILABLE_MESSAGE,
} from "@/lib/agent-presets"
import type { ModelInfo } from "@/lib/chat"
import { getApiErrorDetail } from "@/lib/errors"
import {
  useChatReadiness,
  useListMcpIntegrations,
  useRegistryActions,
  useWorkspaceAgentModels,
} from "@/lib/hooks"
import { cn, slugify } from "@/lib/utils"
import { useWorkspaceId } from "@/providers/workspace-id"

const DATA_TYPE_OUTPUT_TYPES = [
  { label: "String", value: "str", icon: Type },
  { label: "Boolean", value: "bool", icon: ToggleLeft },
  { label: "Integer", value: "int", icon: Hash },
  { label: "Float", value: "float", icon: Percent },
  { label: "List of booleans", value: "list[bool]", icon: ListTodo },
  { label: "List of floats", value: "list[float]", icon: Brackets },
  { label: "List of integers", value: "list[int]", icon: ListOrdered },
  { label: "List of strings", value: "list[str]", icon: List },
] as const

const NEW_PRESET_ID = "new"
const DEFAULT_RETRIES = 3
const SUBAGENT_ALIAS_REGEX = /^[a-z0-9](?:[a-z0-9-]*[a-z0-9])?$/
const POSITIVE_INTEGER_REGEX = /^[1-9]\d*$/
const RESERVED_SUBAGENT_ALIASES = new Set([
  "agent",
  "general-purpose",
  "root",
  "task",
])

/**
 * Maps MCP integration slugs to provider IDs for icon lookup.
 * This handles both built-in MCP providers and custom integrations.
 */
function getMcpProviderId(slug: string): string | undefined {
  // Map common slugs to provider IDs
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

const agentPresetSchema = z
  .object({
    name: z.string().trim().min(1, "Name is required"),
    slug: z.string().trim().min(1, "Slug is required"),
    description: z.string().max(1000).optional(),
    instructions: z.string().optional(),
    source_id: z.string().optional(),
    catalog_id: z.string().optional(),
    model_provider: z.string().trim().min(1, "Model provider is required"),
    model_name: z.string().trim().min(1, "Model name is required"),
    base_url: z.union([z.string().url(), z.literal(""), z.undefined()]),
    outputTypeKind: z.enum(["none", "data-type", "json"]),
    outputTypeDataType: z.string().optional(),
    outputTypeJson: z.string().optional(),
    actions: z.array(z.string()).default([]),
    namespaces: z.array(z.string()).default([]),
    mcpIntegrations: z.array(z.string()).default([]),
    agentsEnabled: z.boolean().default(false),
    subagents: z
      .array(
        z.object({
          preset: z.string().default(""),
          name: z.string().default(""),
          description: z.string().max(1000).default(""),
          presetVersion: z.string().default(""),
          maxTurns: z.string().default(""),
        })
      )
      .default([]),
    skills: z
      .array(
        z.object({
          skillId: z.string().trim().min(1, "Select a skill"),
          skillVersionId: z.string().trim().min(1, "Select a version"),
        })
      )
      .default([]),
    toolApprovals: z
      .array(
        z.object({
          tool: z.string().trim().min(1, "Tool name is required"),
          allow: z.boolean(),
        })
      )
      .default([]),
    retries: z.coerce
      .number({ invalid_type_error: "Retries must be a number" })
      .int()
      .min(0, "Retries must be 0 or more"),
    enableThinking: z.boolean().default(true),
    enableInternetAccess: z.boolean().default(false),
  })
  .superRefine((data, ctx) => {
    if (data.outputTypeKind === "data-type" && !data.outputTypeDataType) {
      ctx.addIssue({
        path: ["outputTypeDataType"],
        code: z.ZodIssueCode.custom,
        message: "Select an output type",
      })
    }
    if (data.outputTypeKind === "json") {
      if (!data.outputTypeJson || data.outputTypeJson.trim().length === 0) {
        ctx.addIssue({
          path: ["outputTypeJson"],
          code: z.ZodIssueCode.custom,
          message: "Provide a JSON schema",
        })
      } else {
        try {
          const parsed = JSON.parse(data.outputTypeJson)
          if (
            parsed === null ||
            Array.isArray(parsed) ||
            typeof parsed !== "object"
          ) {
            ctx.addIssue({
              path: ["outputTypeJson"],
              code: z.ZodIssueCode.custom,
              message: "JSON schema must be an object",
            })
          }
        } catch (_error) {
          ctx.addIssue({
            path: ["outputTypeJson"],
            code: z.ZodIssueCode.custom,
            message: "Invalid JSON",
          })
        }
      }
    }

    if (data.agentsEnabled) {
      const aliases = new Set<string>()
      data.subagents.forEach((subagent, index) => {
        const preset = subagent.preset.trim()
        const alias = subagent.name.trim()
        const effectiveAlias = alias || preset

        if (!preset) {
          ctx.addIssue({
            path: ["subagents", index, "preset"],
            code: z.ZodIssueCode.custom,
            message: "Select a preset",
          })
        }
        if (alias && !SUBAGENT_ALIAS_REGEX.test(alias)) {
          ctx.addIssue({
            path: ["subagents", index, "name"],
            code: z.ZodIssueCode.custom,
            message:
              "Use lowercase letters, numbers, and hyphens; start and end with a letter or number",
          })
        }
        if (effectiveAlias && RESERVED_SUBAGENT_ALIASES.has(effectiveAlias)) {
          ctx.addIssue({
            path: ["subagents", index, alias ? "name" : "preset"],
            code: z.ZodIssueCode.custom,
            message: "This alias is reserved",
          })
        }
        if (effectiveAlias && aliases.has(effectiveAlias)) {
          ctx.addIssue({
            path: ["subagents", index, alias ? "name" : "preset"],
            code: z.ZodIssueCode.custom,
            message: "Subagent aliases must be unique",
          })
        }
        if (effectiveAlias) {
          aliases.add(effectiveAlias)
        }
        if (
          subagent.presetVersion.trim() &&
          !POSITIVE_INTEGER_REGEX.test(subagent.presetVersion.trim())
        ) {
          ctx.addIssue({
            path: ["subagents", index, "presetVersion"],
            code: z.ZodIssueCode.custom,
            message: "Use a positive version number",
          })
        }
        if (
          subagent.maxTurns.trim() &&
          !POSITIVE_INTEGER_REGEX.test(subagent.maxTurns.trim())
        ) {
          ctx.addIssue({
            path: ["subagents", index, "maxTurns"],
            code: z.ZodIssueCode.custom,
            message: "Use a positive turn limit",
          })
        }
      })
    }
  })

type AgentPresetFormValues = z.infer<typeof agentPresetSchema>
type SkillBindingFormValue = AgentPresetFormValues["skills"][number]
type ToolApprovalFormValue = AgentPresetFormValues["toolApprovals"][number]

const DEFAULT_FORM_VALUES: AgentPresetFormValues = {
  name: "",
  slug: "",
  description: "",
  instructions: "",
  source_id: "",
  catalog_id: "",
  model_provider: "",
  model_name: "",
  base_url: "",
  outputTypeKind: "none",
  outputTypeDataType: "",
  outputTypeJson: "",
  actions: [],
  namespaces: [],
  mcpIntegrations: [],
  agentsEnabled: false,
  subagents: [],
  skills: [],
  toolApprovals: [],
  retries: DEFAULT_RETRIES,
  enableThinking: true,
  enableInternetAccess: false,
}

export function AgentPresetsBuilder({
  presetId,
  builderPrompt,
}: {
  presetId?: string
  builderPrompt?: string
}) {
  const router = useRouter()
  const workspaceId = useWorkspaceId()
  const { hasEntitlement, isLoading: entitlementsLoading } = useEntitlements()
  const agentAddonsEnabled = hasEntitlement("agent_addons")
  const activePresetId = presetId ?? NEW_PRESET_ID

  const { presets, presetsIsLoading, presetsError } = useAgentPresets(
    workspaceId,
    { enabled: agentAddonsEnabled && !entitlementsLoading }
  )
  const { registryActions } = useRegistryActions()
  const { models, providers } = useWorkspaceAgentModels(workspaceId)
  const enabledModelsLoaded = models !== undefined

  const { mcpIntegrations, mcpIntegrationsIsLoading } =
    useListMcpIntegrations(workspaceId)

  const mcpIntegrationsForForm = useMemo(() => {
    if (!mcpIntegrations) {
      return []
    }

    return mcpIntegrations
      .map((integration) => ({
        id: integration.id,
        name: integration.name,
        description: integration.description,
        providerId: getMcpProviderId(integration.slug),
      }))
      .sort((a, b) => a.name.localeCompare(b.name))
  }, [mcpIntegrations])

  const { createAgentPreset, createAgentPresetIsPending } =
    useCreateAgentPreset(workspaceId)
  const { updateAgentPreset, updateAgentPresetIsPending } =
    useUpdateAgentPreset(workspaceId)
  const { deleteAgentPreset, deleteAgentPresetIsPending } =
    useDeleteAgentPreset(workspaceId)

  const handleSetSelectedPresetId = useCallback(
    (nextId: string) => {
      if (!workspaceId) {
        return
      }
      const normalizedId = nextId?.trim() ? nextId : NEW_PRESET_ID
      if (normalizedId === activePresetId) {
        return
      }
      const nextPath = `/workspaces/${workspaceId}/agents/${normalizedId}`
      router.replace(nextPath)
    },
    [activePresetId, router, workspaceId]
  )

  // Fetch full preset data when a preset is selected (not in create mode)
  const selectedPresetId =
    activePresetId === NEW_PRESET_ID ? null : activePresetId
  const { preset: selectedPreset } = useAgentPreset(
    workspaceId,
    selectedPresetId,
    {
      enabled: agentAddonsEnabled && !entitlementsLoading,
    }
  )

  useEffect(() => {
    if (
      !presetId ||
      presetId === NEW_PRESET_ID ||
      presetsIsLoading ||
      !presets
    ) {
      return
    }
    const presetExists = presets.some((preset) => preset.id === presetId)
    if (presetExists) {
      return
    }
    if (presets.length > 0) {
      handleSetSelectedPresetId(presets[0].id)
    } else {
      handleSetSelectedPresetId(NEW_PRESET_ID)
    }
  }, [presets, handleSetSelectedPresetId, presetId, presetsIsLoading])

  const actionSuggestions: Suggestion[] = useMemo(() => {
    if (!registryActions) {
      return []
    }
    return registryActions
      .map((action) => ({
        id: action.id,
        label: action.default_title ?? action.name,
        value: action.action,
        description: action.description,
        group: action.namespace,
        icon: getIcon(action.action, {
          className: "size-6 p-[3px] border-[0.5px]",
        }),
      }))
      .sort((a, b) => a.label.localeCompare(b.label))
  }, [registryActions])

  const namespaceSuggestions: Suggestion[] = useMemo(() => {
    if (!registryActions) {
      return []
    }
    const seen = new Set<string>()
    const entries: Suggestion[] = []
    for (const action of registryActions) {
      if (action.namespace && !seen.has(action.namespace)) {
        seen.add(action.namespace)
        entries.push({
          id: action.namespace,
          label: action.namespace,
          value: action.namespace,
        })
      }
    }
    return entries.sort((a, b) => a.label.localeCompare(b.label))
  }, [registryActions])

  const enabledModelOptions = useMemo(
    () => buildEnabledModelOptions(models, providers),
    [models, providers]
  )

  if (presetsIsLoading) {
    return <CenteredSpinner />
  }

  if (presetsError) {
    const detail =
      typeof presetsError.body?.detail === "string"
        ? presetsError.body.detail
        : presetsError.message
    return (
      <div className="flex h-full items-center justify-center px-6">
        <Alert variant="destructive" className="max-w-xl">
          <AlertTitle>Unable to load agent presets</AlertTitle>
          <AlertDescription>{detail}</AlertDescription>
        </Alert>
      </div>
    )
  }

  return (
    <div className="flex h-full w-full flex-col overflow-hidden">
      <AgentPresetForm
        key={selectedPreset?.id ?? NEW_PRESET_ID}
        preset={selectedPreset ?? null}
        mode={selectedPreset ? "edit" : "create"}
        workspaceId={workspaceId}
        agentPresets={presets ?? []}
        actionSuggestions={actionSuggestions}
        namespaceSuggestions={namespaceSuggestions}
        enabledModelOptions={enabledModelOptions}
        enabledModelsLoaded={enabledModelsLoaded}
        mcpIntegrations={mcpIntegrationsForForm}
        mcpIntegrationsIsLoading={mcpIntegrationsIsLoading}
        isSaving={
          selectedPreset
            ? updateAgentPresetIsPending
            : createAgentPresetIsPending
        }
        isDeleting={deleteAgentPresetIsPending}
        onCreate={async (payload) => {
          const created = await createAgentPreset(payload)
          handleSetSelectedPresetId(created.id)
          return created
        }}
        onUpdate={async (presetId, payload) => {
          const updated = await updateAgentPreset({
            presetId,
            ...payload,
          })
          handleSetSelectedPresetId(updated.id)
          return updated
        }}
        onDuplicate={
          selectedPreset
            ? async () => {
                const existingSlugs =
                  presets
                    ?.map((preset) => preset.slug)
                    .filter(
                      (slug): slug is string => typeof slug === "string"
                    ) ?? []
                const created = await createAgentPreset(
                  buildDuplicateAgentPresetPayload(
                    selectedPreset,
                    existingSlugs
                  )
                )
                handleSetSelectedPresetId(created.id)
              }
            : undefined
        }
        onDelete={
          selectedPreset
            ? async () => {
                await deleteAgentPreset({
                  presetId: selectedPreset.id,
                  presetName: selectedPreset.name,
                })
                const remaining =
                  presets?.filter(
                    (preset) => preset.id !== selectedPreset.id
                  ) ?? []
                if (remaining.length > 0) {
                  handleSetSelectedPresetId(remaining[0].id)
                } else {
                  handleSetSelectedPresetId(NEW_PRESET_ID)
                }
              }
            : undefined
        }
      />
    </div>
  )
}

function AgentPresetChatPane({
  preset,
  workspaceId,
  enabledModelOptions,
  enabledModelsLoaded,
}: {
  preset: AgentPresetRead | null
  workspaceId: string
  enabledModelOptions: EnabledModelOption[]
  enabledModelsLoaded: boolean
}) {
  const [selectedChatId, setSelectedChatId] = useState<string | null>(null)

  const { chats, chatsLoading, chatsError, refetchChats } = useListChats(
    {
      workspaceId,
      entityType: "agent_preset",
      entityId: preset?.id,
    },
    { enabled: Boolean(preset && workspaceId) }
  )

  useEffect(() => {
    setSelectedChatId(null)
  }, [preset?.id])

  const latestChatId = chats?.[0]?.id
  const activeChatId = selectedChatId ?? latestChatId

  const { createChat, createChatPending } = useCreateChat(workspaceId)
  const { updateChat, isUpdating } = useUpdateChat(workspaceId)
  const { chat, chatLoading, chatError } = useGetChatVercel({
    chatId: activeChatId,
    workspaceId,
  })
  const { versions, versionsIsLoading, versionsError } = useAgentPresetVersions(
    workspaceId,
    preset?.id,
    { enabled: Boolean(preset && workspaceId) }
  )
  const selectedVersionId = chat?.agent_preset_version_id ?? null
  const currentVersionId =
    preset?.current_version_id ?? versions?.[0]?.id ?? null
  const {
    presetVersion: selectedVersionConfig,
    presetVersionIsLoading: selectedVersionConfigIsLoading,
  } = useAgentPresetVersion(workspaceId, preset?.id, selectedVersionId, {
    enabled: Boolean(workspaceId && preset?.id && selectedVersionId),
  })
  const newChatVersionId = selectedVersionId ?? currentVersionId
  const effectiveModelConfig = selectedVersionId
    ? (selectedVersionConfig ?? null)
    : preset
  const selectedModel = useMemo(
    () =>
      effectiveModelConfig
        ? findEnabledModelOption(enabledModelOptions, {
            modelProvider: effectiveModelConfig.model_provider,
            modelName: effectiveModelConfig.model_name,
            baseUrl: effectiveModelConfig.base_url ?? null,
          })
        : null,
    [effectiveModelConfig, enabledModelOptions]
  )
  const hasLegacyModelConfig = Boolean(
    effectiveModelConfig?.model_provider && effectiveModelConfig.model_name
  )

  const modelInfo: ModelInfo | null = useMemo(() => {
    if (!effectiveModelConfig) {
      return null
    }

    const provider =
      selectedModel?.modelProvider ?? effectiveModelConfig.model_provider

    return {
      name: selectedModel?.modelName ?? effectiveModelConfig.model_name,
      provider,
      baseUrl: selectedModel?.baseUrl ?? effectiveModelConfig.base_url ?? null,
      iconId: selectedModel?.iconId ?? getProviderIconId(provider),
    }
  }, [effectiveModelConfig, selectedModel])

  const canStartChat = Boolean(
    preset &&
      effectiveModelConfig &&
      (!enabledModelsLoaded || selectedModel !== null || hasLegacyModelConfig)
  )
  const shouldAutoCreateChat =
    canStartChat && !activeChatId && !chatsLoading && !createChatPending

  const handleCreateChat = async () => {
    if (!preset || createChatPending) {
      return
    }

    try {
      const newChat = await createChat({
        title: `${preset.name} chat`,
        entity_type: "agent_preset",
        entity_id: preset.id,
        tools: selectedVersionConfig?.actions ?? preset.actions ?? undefined,
        agent_preset_id: preset.id,
        agent_preset_version_id: newChatVersionId,
      })
      setSelectedChatId(newChat.id)
      await refetchChats()
    } catch (error) {
      console.error("Failed to create agent preset chat", error)
    }
  }

  const handlePresetVersionChange = async (nextVersionId: string | null) => {
    if (!activeChatId) {
      return
    }

    try {
      await updateChat({
        chatId: activeChatId,
        update: {
          agent_preset_version_id: nextVersionId,
        },
      })
    } catch (error) {
      console.error("Failed to update preset chat version", error)
    }
  }

  // Auto-create chat when preset is ready and no chat exists
  useEffect(() => {
    if (shouldAutoCreateChat) {
      void handleCreateChat()
    }
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [shouldAutoCreateChat])

  const renderBody = () => {
    if (!preset) {
      return (
        <div className="flex h-full items-center justify-center px-4">
          <Empty>
            <EmptyHeader>
              <EmptyMedia variant="icon">
                <MessageCircle />
              </EmptyMedia>
              <EmptyTitle>Live chat</EmptyTitle>
              <EmptyDescription>
                Save the agent to start chatting with it.
              </EmptyDescription>
            </EmptyHeader>
          </Empty>
        </div>
      )
    }

    if (selectedVersionConfigIsLoading) {
      return (
        <div className="flex h-full items-center justify-center">
          <CenteredSpinner />
        </div>
      )
    }

    if (enabledModelsLoaded && !selectedModel && !hasLegacyModelConfig) {
      return (
        <div className="flex h-full flex-col items-center justify-center px-4">
          <div className="flex max-w-xs flex-col items-center gap-2 text-center text-xs text-muted-foreground">
            <AlertCircle className="size-5 text-amber-500" />
            <p className="text-pretty">
              This preset no longer points at an enabled model. Select a new
              model in the preset configuration before starting chat.
            </p>
          </div>
        </div>
      )
    }

    if (chatsError) {
      return (
        <div className="flex h-full items-center justify-center px-4">
          <Alert variant="destructive" className="w-full text-xs">
            <AlertTitle>Unable to load chat sessions</AlertTitle>
            <AlertDescription>
              {typeof chatsError.message === "string"
                ? chatsError.message
                : "Something went wrong while fetching the chat session."}
            </AlertDescription>
          </Alert>
        </div>
      )
    }

    if (!activeChatId || chatLoading || chatsLoading || !chat || !modelInfo) {
      return (
        <div className="flex h-full items-center justify-center">
          <CenteredSpinner />
        </div>
      )
    }

    if (chatError) {
      return (
        <div className="flex h-full items-center justify-center px-4">
          <Alert variant="destructive" className="w-full text-xs">
            <AlertTitle>Chat unavailable</AlertTitle>
            <AlertDescription>
              {typeof chatError.message === "string"
                ? chatError.message
                : "We couldn't load the conversation for this agent."}
            </AlertDescription>
          </Alert>
        </div>
      )
    }

    return (
      <ChatSessionPane
        chat={chat}
        workspaceId={workspaceId}
        entityType={"agent_preset"}
        entityId={preset.id}
        className="flex-1 min-h-0"
        placeholder={`Talk to ${preset.name}...`}
        modelInfo={modelInfo}
        toolsEnabled={false}
      />
    )
  }

  return (
    <div className="flex h-full flex-col bg-background">
      {preset ? (
        <div className="flex h-10 items-center justify-between gap-3 border-b px-3">
          <AgentPresetVersionSelect
            versions={versions}
            versionsIsLoading={versionsIsLoading}
            versionsError={versionsError}
            selectedVersionId={chat?.agent_preset_version_id ?? null}
            currentVersionId={preset.current_version_id ?? null}
            onSelect={handlePresetVersionChange}
            disabled={!activeChatId || !chat || isUpdating}
            triggerClassName="h-8 w-[10.5rem] text-xs"
          />
          <div className="flex items-center gap-1">
            <ChatHistoryDropdown
              chats={chats}
              isLoading={chatsLoading}
              error={chatsError}
              selectedChatId={activeChatId ?? undefined}
              onSelectChat={(chatId) => setSelectedChatId(chatId)}
              align="end"
            />
            <Button
              size="sm"
              variant="ghost"
              className="text-xs"
              disabled={createChatPending || !canStartChat}
              onClick={() => void handleCreateChat()}
            >
              {createChatPending ? (
                <Loader2 className="mr-2 size-4 animate-spin" />
              ) : (
                <Plus className="mr-2 size-4" />
              )}
              New chat
            </Button>
          </div>
        </div>
      ) : null}
      <div className="flex-1 min-h-0">{renderBody()}</div>
    </div>
  )
}

function AutoResizeTextarea({
  value,
  onChange,
  onBlur,
  disabled,
  placeholder,
  className,
}: {
  value: string
  onChange: (e: React.ChangeEvent<HTMLTextAreaElement>) => void
  onBlur: () => void
  disabled?: boolean
  placeholder?: string
  className?: string
}) {
  const textareaRef = useRef<HTMLTextAreaElement>(null)

  useEffect(() => {
    const textarea = textareaRef.current
    if (textarea) {
      textarea.style.height = "auto"
      textarea.style.height = `${Math.max(
        textarea.scrollHeight,
        2 * parseFloat(getComputedStyle(textarea).lineHeight)
      )}px`
    }
  }, [value])

  const handleChange = (e: React.ChangeEvent<HTMLTextAreaElement>) => {
    onChange(e)
    const textarea = e.target
    textarea.style.height = "auto"
    textarea.style.height = `${Math.max(
      textarea.scrollHeight,
      2 * parseFloat(getComputedStyle(textarea).lineHeight)
    )}px`
  }

  return (
    <Textarea
      ref={textareaRef}
      className={className}
      placeholder={placeholder}
      value={value}
      onChange={handleChange}
      onBlur={onBlur}
      disabled={disabled}
    />
  )
}

type AgentPresetSideTab =
  | "live-chat"
  | "assistant"
  | "configuration"
  | "subagents"
  | "skills"
  | "channels"
  | "structured-output"
  | "versions"

function getAgentPresetErrorTab(
  errors: FieldErrors<AgentPresetFormValues>
): AgentPresetSideTab | null {
  if (
    errors.outputTypeKind ||
    errors.outputTypeDataType ||
    errors.outputTypeJson
  ) {
    return "structured-output"
  }

  if (errors.skills) {
    return "skills"
  }

  if (errors.agentsEnabled || errors.subagents) {
    return "subagents"
  }

  if (
    errors.actions ||
    errors.mcpIntegrations ||
    errors.namespaces ||
    errors.toolApprovals ||
    errors.retries ||
    errors.enableInternetAccess
  ) {
    return "configuration"
  }
  if (
    errors.instructions ||
    errors.model_provider ||
    errors.model_name ||
    errors.base_url
  ) {
    return "assistant"
  }
  if (errors.name || errors.slug || errors.description) {
    return "live-chat"
  }
  return null
}

type McpIntegrationOption = {
  id: string
  name: string
  description?: string | null
  providerId?: string
}

type EnabledModelOption = {
  catalogId: string
  sourceId: string | null
  modelName: string
  modelProvider: string
  iconId: string
  displayName: string
  label: string
  metadata: string
  sourceName: string
  sourceType: string
  baseUrl?: string | null
}

function getModelSelectionKey(selection: {
  source_id?: string | null
  model_provider?: string | null
  model_name?: string | null
}): string {
  return `${selection.source_id ?? "platform"}::${selection.model_provider ?? ""}::${selection.model_name ?? ""}`
}

function buildEnabledModelOptions(
  models: AgentCatalogRead[] | undefined,
  providers: AgentCustomProviderRead[] | undefined
): EnabledModelOption[] {
  const providersById = new Map(
    (providers ?? []).map((provider) => [provider.id, provider])
  )

  return (models ?? [])
    .map((model) => {
      const provider = model.custom_provider_id
        ? (providersById.get(model.custom_provider_id) ?? null)
        : null
      const isCustomSource = model.custom_provider_id != null
      const sourceName = isCustomSource
        ? (provider?.display_name ?? "Custom")
        : getProviderDisplayLabel(model.model_provider)
      const sourceType = isCustomSource
        ? "custom"
        : model.organization_id
          ? "organization"
          : "platform"

      return {
        catalogId: model.id,
        sourceId: model.custom_provider_id,
        modelName: model.model_name,
        modelProvider: model.model_provider,
        iconId: getProviderIconId(model.model_provider),
        displayName: model.model_name,
        label: model.model_name,
        metadata: model.model_provider,
        sourceName,
        sourceType,
        baseUrl: provider?.base_url ?? null,
      }
    })
    .sort((a, b) => {
      const sourceComparison = a.sourceName.localeCompare(b.sourceName)
      if (sourceComparison !== 0) {
        return sourceComparison
      }
      return a.displayName.localeCompare(b.displayName)
    })
}

function getProviderIconId(provider: string): string {
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

function findEnabledModelOption(
  options: EnabledModelOption[],
  selection: {
    sourceId?: string | null
    modelProvider?: string | null
    modelName?: string | null
    baseUrl?: string | null
  }
): EnabledModelOption | null {
  if (!selection.modelProvider || !selection.modelName) {
    return null
  }

  const normalizedSourceId = selection.sourceId ?? null
  const normalizedBaseUrl = normalizeOptional(selection.baseUrl)
  return (
    options.find(
      (option) =>
        option.sourceId === normalizedSourceId &&
        option.modelProvider === selection.modelProvider &&
        option.modelName === selection.modelName
    ) ??
    options.find(
      (option) =>
        option.modelProvider === selection.modelProvider &&
        option.modelName === selection.modelName &&
        normalizeOptional(option.baseUrl) === normalizedBaseUrl
    ) ??
    null
  )
}

function syncFormModelSelection(
  form: UseFormReturn<AgentPresetFormValues>,
  option: EnabledModelOption,
  shouldDirty: boolean
) {
  form.setValue("source_id", option.sourceId ?? "", { shouldDirty })
  form.setValue("catalog_id", option.catalogId, { shouldDirty })
  form.setValue("model_provider", option.modelProvider, { shouldDirty })
  form.setValue("model_name", option.modelName, { shouldDirty })
  form.setValue("base_url", option.baseUrl ?? "", { shouldDirty })
}

type AgentPresetFormProps = {
  preset: AgentPresetRead | null
  mode: AgentPresetFormMode
  workspaceId: string
  agentPresets: AgentPresetReadMinimal[]
  builderPrompt?: string
  onCreate: (payload: AgentPresetCreate) => Promise<AgentPresetRead>
  onUpdate: (
    presetId: string,
    payload: AgentPresetUpdate
  ) => Promise<AgentPresetRead>
  onDuplicate?: () => Promise<void>
  onDelete?: () => Promise<void>
  isSaving: boolean
  isDeleting: boolean
  actionSuggestions: Suggestion[]
  namespaceSuggestions: Suggestion[]
  enabledModelOptions: EnabledModelOption[]
  enabledModelsLoaded: boolean
  mcpIntegrations: McpIntegrationOption[]
  mcpIntegrationsIsLoading: boolean
}

function AgentPresetForm({
  preset,
  mode,
  workspaceId,
  agentPresets,
  builderPrompt,
  onCreate,
  onUpdate,
  onDuplicate,
  onDelete,
  isSaving,
  isDeleting,
  actionSuggestions,
  namespaceSuggestions,
  enabledModelOptions,
  enabledModelsLoaded,
  mcpIntegrations,
  mcpIntegrationsIsLoading,
}: AgentPresetFormProps) {
  const [deleteDialogOpen, setDeleteDialogOpen] = useState(false)
  const [isDuplicating, setIsDuplicating] = useState(false)
  const isDuplicatingRef = useRef(false)
  const [activeTab, setActiveTab] = useState<AgentPresetSideTab>("live-chat")
  const { isFeatureEnabled: isFeatureEnabledFlag } = useFeatureFlag()
  const channelsEnabled = isFeatureEnabledFlag("agent-channels")
  const unavailableSubagentPresetSlugs = useMemo(
    () => getUnavailableSubagentPresetSlugs(agentPresets),
    [agentPresets]
  )

  const form = useForm<AgentPresetFormValues>({
    resolver: zodResolver(agentPresetSchema),
    mode: "onBlur",
    defaultValues: preset ? presetToFormValues(preset) : DEFAULT_FORM_VALUES,
  })

  const handleConfirmDelete = async () => {
    if (!onDelete) {
      return
    }
    try {
      await onDelete()
      setDeleteDialogOpen(false)
    } catch (error) {
      console.error("Failed to delete agent preset", error)
    }
  }

  const {
    fields: skillFields,
    append: appendSkillBinding,
    remove: removeSkillBinding,
  } = useFieldArray({
    control: form.control,
    name: "skills",
  })

  const {
    fields: toolApprovalFields,
    append: appendToolApproval,
    remove: removeToolApproval,
  } = useFieldArray({
    control: form.control,
    name: "toolApprovals",
  })
  const {
    fields: subagentFields,
    append: appendSubagent,
    remove: removeSubagent,
  } = useFieldArray({
    control: form.control,
    name: "subagents",
  })

  useEffect(() => {
    const defaults = preset ? presetToFormValues(preset) : DEFAULT_FORM_VALUES
    form.reset(defaults, { keepDirty: false })
  }, [form, mode, preset])

  const watchedName = form.watch("name")
  const sourceId = form.watch("source_id")
  const modelProvider = form.watch("model_provider")
  const modelName = form.watch("model_name")
  const baseUrl = form.watch("base_url")
  const selectedModel = useMemo(
    () =>
      findEnabledModelOption(enabledModelOptions, {
        sourceId,
        modelProvider,
        modelName,
        baseUrl,
      }),
    [baseUrl, enabledModelOptions, sourceId, modelName, modelProvider]
  )

  useEffect(() => {
    const nextSlug = slugify(watchedName ?? "", "-")
    if (form.getValues("slug") !== nextSlug) {
      form.setValue("slug", nextSlug, { shouldDirty: false })
    }
  }, [form, watchedName])

  useEffect(() => {
    if (!enabledModelsLoaded) {
      return
    }
    if (selectedModel) {
      syncFormModelSelection(form, selectedModel, false)
      return
    }
    if (preset) {
      if (form.getValues("source_id")) {
        form.setValue("source_id", "", { shouldDirty: false })
      }
      if (form.getValues("catalog_id")) {
        form.setValue("catalog_id", "", { shouldDirty: false })
      }
      return
    }
    if (
      form.getValues("source_id") ||
      form.getValues("model_provider") ||
      form.getValues("model_name") ||
      form.getValues("base_url")
    ) {
      form.setValue("source_id", "", { shouldDirty: false })
      form.setValue("model_provider", "", { shouldDirty: false })
      form.setValue("model_name", "", { shouldDirty: false })
      form.setValue("base_url", "", { shouldDirty: false })
    }
  }, [enabledModelsLoaded, form, preset, selectedModel])

  const effectiveTab =
    !channelsEnabled && activeTab === "channels" ? "live-chat" : activeTab

  const handleSubmit = form.handleSubmit(
    async (values) => {
      if (values.agentsEnabled) {
        const unavailableIndex = values.subagents.findIndex((subagent) =>
          unavailableSubagentPresetSlugs.has(subagent.preset.trim())
        )
        if (unavailableIndex >= 0) {
          form.setError(`subagents.${unavailableIndex}.preset`, {
            type: "manual",
            message: SUBAGENT_APPROVAL_UNAVAILABLE_MESSAGE,
          })
          setActiveTab("subagents")
          return
        }
      }

      const payload = formValuesToPayload(values)
      if (mode === "edit" && preset) {
        const updated = await onUpdate(preset.id, payload)
        form.reset(presetToFormValues(updated))
      } else {
        const created = await onCreate(payload)
        form.reset(presetToFormValues(created))
      }
    },
    (errors) => {
      const nextTab = getAgentPresetErrorTab(errors)
      if (nextTab) {
        setActiveTab(nextTab)
      }
    }
  )

  const canSubmit =
    form.formState.isDirty ||
    (mode === "create" &&
      Boolean(form.watch("name")) &&
      Boolean(form.watch("model_provider")) &&
      Boolean(form.watch("model_name")))

  const handleDeleteDialogChange = useCallback(
    (nextOpen: boolean) => {
      if (isDeleting) {
        return
      }
      setDeleteDialogOpen(nextOpen)
    },
    [isDeleting]
  )

  const handleDuplicate = useCallback(async () => {
    if (!onDuplicate || isDuplicatingRef.current) {
      return
    }

    isDuplicatingRef.current = true
    setIsDuplicating(true)
    try {
      await onDuplicate()
    } catch (error) {
      console.error("Failed to duplicate agent preset", error)
      toast({
        title: "Duplicate failed",
        description: "Could not duplicate agent. Please try again.",
        variant: "destructive",
      })
    } finally {
      isDuplicatingRef.current = false
      setIsDuplicating(false)
    }
  }, [onDuplicate])

  const handleAddToolApproval = useCallback(() => {
    appendToolApproval({
      tool: "",
      allow: true,
    })
  }, [appendToolApproval])

  const handleAddSubagent = useCallback(() => {
    appendSubagent({
      preset: "",
      name: "",
      description: "",
      presetVersion: "",
      maxTurns: "",
    })
  }, [appendSubagent])

  const handleAddSkillBinding = useCallback(
    (binding: SkillBindingFormValue) => {
      appendSkillBinding(binding)
    },
    [appendSkillBinding]
  )

  return (
    <Form {...form}>
      <form
        onSubmit={(event) => {
          event.preventDefault()
          // Ignore submits bubbling from nested forms (e.g., chat prompt inputs).
          if (event.target !== event.currentTarget) {
            return
          }
          void handleSubmit()
        }}
        className="h-full"
      >
        <ResizablePanelGroup direction="horizontal" className="h-full">
          <ResizablePanel defaultSize={62} minSize={40}>
            <AgentPresetDocumentPanel
              form={form}
              mode={mode}
              isSaving={isSaving}
              isDeleting={isDeleting}
              canSubmit={canSubmit}
              presetName={preset?.name ?? ""}
              onDuplicate={onDuplicate ? handleDuplicate : undefined}
              isDuplicating={isDuplicating}
              onDelete={onDelete}
              deleteDialogOpen={deleteDialogOpen}
              onDeleteDialogChange={handleDeleteDialogChange}
              onConfirmDelete={handleConfirmDelete}
            />
          </ResizablePanel>

          <ResizableHandle withHandle />

          <ResizablePanel defaultSize={38} minSize={26}>
            <AgentPresetRightPanel
              activeTab={effectiveTab}
              onTabChange={setActiveTab}
              channelsEnabled={channelsEnabled}
              preset={preset}
              workspaceId={workspaceId}
              agentPresets={agentPresets}
              builderPrompt={builderPrompt}
              form={form}
              isSaving={isSaving}
              actionSuggestions={actionSuggestions}
              namespaceSuggestions={namespaceSuggestions}
              enabledModelOptions={enabledModelOptions}
              enabledModelsLoaded={enabledModelsLoaded}
              mcpIntegrations={mcpIntegrations}
              mcpIntegrationsIsLoading={mcpIntegrationsIsLoading}
              skillFields={skillFields}
              onAddSkillBinding={handleAddSkillBinding}
              onRemoveSkillBinding={removeSkillBinding}
              toolApprovalFields={toolApprovalFields}
              onAddToolApproval={handleAddToolApproval}
              onRemoveToolApproval={removeToolApproval}
              subagentFields={subagentFields}
              onAddSubagent={handleAddSubagent}
              onRemoveSubagent={removeSubagent}
            />
          </ResizablePanel>
        </ResizablePanelGroup>
      </form>
    </Form>
  )
}

function AgentPresetDocumentPanel({
  form,
  mode,
  isSaving,
  isDeleting,
  canSubmit,
  presetName,
  onDuplicate,
  isDuplicating,
  onDelete,
  deleteDialogOpen,
  onDeleteDialogChange,
  onConfirmDelete,
}: {
  form: UseFormReturn<AgentPresetFormValues>
  mode: AgentPresetFormMode
  isSaving: boolean
  isDeleting: boolean
  canSubmit: boolean
  presetName: string
  onDuplicate?: () => Promise<void>
  isDuplicating: boolean
  onDelete?: () => Promise<void>
  deleteDialogOpen: boolean
  onDeleteDialogChange: (nextOpen: boolean) => void
  onConfirmDelete: () => Promise<void>
}) {
  return (
    <ScrollArea className="h-full">
      <div className="mx-auto flex w-full max-w-4xl flex-col gap-0 px-10 py-10">
        <div className="flex items-start justify-between gap-4">
          <div className="min-w-0 flex-1 space-y-3">
            <FormField
              control={form.control}
              name="slug"
              render={({ field }) => (
                <FormItem className="space-y-0">
                  <FormLabel className="sr-only">Slug</FormLabel>
                  <FormControl>
                    <input type="hidden" {...field} value={field.value ?? ""} />
                  </FormControl>
                </FormItem>
              )}
            />
            <FormField
              control={form.control}
              name="name"
              render={({ field }) => (
                <FormItem className="space-y-0">
                  <FormLabel className="sr-only">Agent name</FormLabel>
                  <FormControl>
                    <Input
                      className="h-auto w-full border-none bg-transparent px-0 text-3xl font-semibold leading-tight text-foreground shadow-none outline-none transition-none placeholder:text-muted-foreground/40 focus-visible:bg-transparent focus-visible:outline-none focus-visible:ring-0"
                      placeholder="New agent preset"
                      disabled={isSaving}
                      {...field}
                      value={field.value ?? ""}
                    />
                  </FormControl>
                </FormItem>
              )}
            />
            <FormField
              control={form.control}
              name="description"
              render={({ field }) => (
                <FormItem>
                  <FormControl>
                    <AutoResizeTextarea
                      value={field.value ?? ""}
                      onChange={field.onChange}
                      onBlur={field.onBlur}
                      disabled={isSaving}
                      placeholder="Describe this agent."
                      className="w-full resize-none overflow-hidden border-none bg-transparent px-0 text-sm leading-relaxed text-muted-foreground shadow-none outline-none transition-none placeholder:text-muted-foreground/50 focus-visible:bg-transparent focus-visible:outline-none focus-visible:ring-0"
                    />
                  </FormControl>
                </FormItem>
              )}
            />
          </div>
          <div className="flex items-center gap-2">
            <Button
              type="submit"
              variant="ghost"
              size="icon"
              disabled={isSaving || !canSubmit}
              className={cn(
                "h-8 w-8 p-1.5 hover:bg-primary hover:text-primary-foreground",
                canSubmit && !isSaving && "bg-primary text-primary-foreground"
              )}
              aria-label="Save agent preset"
            >
              {isSaving ? (
                <Loader2 className="size-4 animate-spin" />
              ) : (
                <Save className="size-4" />
              )}
            </Button>
            {mode === "edit" && onDelete ? (
              <>
                <DropdownMenu>
                  <DropdownMenuTrigger asChild>
                    <Button
                      type="button"
                      variant="ghost"
                      size="icon"
                      disabled={isDeleting || isSaving}
                      className="h-8 w-8 p-1.5"
                      aria-label="Open agent actions menu"
                    >
                      <MoreVertical className="size-4" />
                    </Button>
                  </DropdownMenuTrigger>
                  <DropdownMenuContent align="end">
                    {onDuplicate ? (
                      <DropdownMenuItem
                        disabled={isDuplicating}
                        onSelect={() => {
                          void onDuplicate()
                        }}
                      >
                        <CopyPlus className="mr-2 size-4" />
                        {isDuplicating ? "Duplicating..." : "Duplicate agent"}
                      </DropdownMenuItem>
                    ) : null}
                    <DropdownMenuSeparator />
                    <DropdownMenuItem
                      className="text-destructive focus:text-destructive"
                      onSelect={(event) => {
                        event.preventDefault()
                        onDeleteDialogChange(true)
                      }}
                    >
                      <Trash2 className="mr-2 size-4" />
                      Delete agent
                    </DropdownMenuItem>
                  </DropdownMenuContent>
                </DropdownMenu>
                <AgentPresetDeleteDialog
                  open={deleteDialogOpen}
                  onOpenChange={onDeleteDialogChange}
                  presetName={presetName}
                  isDeleting={isDeleting}
                  onConfirm={onConfirmDelete}
                />
              </>
            ) : null}
          </div>
        </div>
        <Separator className="my-5" />
        <section className="space-y-4">
          <FormField
            control={form.control}
            name="instructions"
            render={({ field }) => (
              <FormItem>
                <FormControl>
                  <SimpleEditor
                    value={field.value ?? ""}
                    onChange={field.onChange}
                    onBlur={field.onBlur}
                    placeholder="You are a helpful analyst..."
                    editable={!isSaving}
                    showToolbar={false}
                    className="[&_.simple-editor-content_.tiptap.ProseMirror.simple-editor]:min-h-[560px] [&_.simple-editor-content_.tiptap.ProseMirror.simple-editor]:text-base [&_.simple-editor-content_.tiptap.ProseMirror.simple-editor]:leading-8"
                  />
                </FormControl>
              </FormItem>
            )}
          />
        </section>
      </div>
    </ScrollArea>
  )
}

function AgentPresetRightPanel({
  activeTab,
  onTabChange,
  channelsEnabled,
  preset,
  workspaceId,
  agentPresets,
  builderPrompt,
  form,
  isSaving,
  actionSuggestions,
  namespaceSuggestions,
  enabledModelOptions,
  enabledModelsLoaded,
  mcpIntegrations,
  mcpIntegrationsIsLoading,
  skillFields,
  onAddSkillBinding,
  onRemoveSkillBinding,
  toolApprovalFields,
  onAddToolApproval,
  onRemoveToolApproval,
  subagentFields,
  onAddSubagent,
  onRemoveSubagent,
}: {
  activeTab: AgentPresetSideTab
  onTabChange: (tab: AgentPresetSideTab) => void
  channelsEnabled: boolean
  preset: AgentPresetRead | null
  workspaceId: string
  agentPresets: AgentPresetReadMinimal[]
  builderPrompt?: string
  form: UseFormReturn<AgentPresetFormValues>
  isSaving: boolean
  actionSuggestions: Suggestion[]
  namespaceSuggestions: Suggestion[]
  enabledModelOptions: EnabledModelOption[]
  enabledModelsLoaded: boolean
  mcpIntegrations: McpIntegrationOption[]
  mcpIntegrationsIsLoading: boolean
  skillFields: Array<{ id: string }>
  onAddSkillBinding: (binding: SkillBindingFormValue) => void
  onRemoveSkillBinding: (index: number) => void
  toolApprovalFields: Array<{ id: string }>
  onAddToolApproval: () => void
  onRemoveToolApproval: (index: number) => void
  subagentFields: Array<{ id: string }>
  onAddSubagent: () => void
  onRemoveSubagent: (index: number) => void
}) {
  return (
    <div className="flex h-full flex-col overflow-hidden">
      <Tabs
        value={activeTab}
        onValueChange={(value) => onTabChange(value as AgentPresetSideTab)}
        className="flex h-full w-full flex-col"
      >
        <div className="w-full shrink-0">
          <div className="no-scrollbar overflow-x-auto">
            <TabsList className="min-w-max h-9 justify-start rounded-none bg-transparent p-0">
              <TabsTrigger
                className="flex h-full min-w-20 items-center justify-center rounded-none px-3 text-xs data-[state=active]:bg-transparent data-[state=active]:shadow-none"
                value="live-chat"
              >
                <MessageCircle className="mr-2 size-4" />
                <span>Chat</span>
              </TabsTrigger>
              <TabsTrigger
                className="flex h-full min-w-20 items-center justify-center rounded-none px-3 text-xs data-[state=active]:bg-transparent data-[state=active]:shadow-none"
                value="assistant"
              >
                <Sparkles className="mr-2 size-4" />
                <span>Builder</span>
              </TabsTrigger>
              <TabsTrigger
                className="flex h-full min-w-20 items-center justify-center rounded-none px-3 text-xs data-[state=active]:bg-transparent data-[state=active]:shadow-none"
                value="configuration"
              >
                <SlidersHorizontal className="mr-2 size-4" />
                <span>Tools</span>
              </TabsTrigger>
              <TabsTrigger
                className="flex h-full min-w-20 items-center justify-center rounded-none px-3 text-xs data-[state=active]:bg-transparent data-[state=active]:shadow-none"
                value="subagents"
              >
                <Bot className="mr-2 size-4" />
                <span>Subagents</span>
              </TabsTrigger>
              <TabsTrigger
                className="flex h-full min-w-20 items-center justify-center rounded-none px-3 text-xs data-[state=active]:bg-transparent data-[state=active]:shadow-none"
                value="skills"
              >
                <Pyramid className="mr-2 size-4" />
                <span>Skills</span>
              </TabsTrigger>
              <TabsTrigger
                className="flex h-full min-w-20 items-center justify-center rounded-none px-3 text-xs data-[state=active]:bg-transparent data-[state=active]:shadow-none"
                value="structured-output"
              >
                <Box className="mr-2 size-4" />
                <span>Output</span>
              </TabsTrigger>
              <TabsTrigger
                className="flex h-full min-w-20 items-center justify-center rounded-none px-3 text-xs data-[state=active]:bg-transparent data-[state=active]:shadow-none"
                value="versions"
              >
                <History className="mr-2 size-4" />
                <span>Versions</span>
              </TabsTrigger>
              {channelsEnabled ? (
                <TabsTrigger
                  className="flex h-full min-w-20 items-center justify-center rounded-none px-3 text-xs data-[state=active]:bg-transparent data-[state=active]:shadow-none"
                  value="channels"
                >
                  <Webhook className="mr-2 size-4" />
                  <span>Channels</span>
                </TabsTrigger>
              ) : null}
            </TabsList>
          </div>
          <Separator />
        </div>

        <div className="flex-1 overflow-hidden">
          <TabsContent value="live-chat" className="mt-0 h-full">
            <AgentPresetChatPane
              preset={preset}
              workspaceId={workspaceId}
              enabledModelOptions={enabledModelOptions}
              enabledModelsLoaded={enabledModelsLoaded}
            />
          </TabsContent>

          <TabsContent value="assistant" className="mt-0 h-full">
            <AgentPresetBuilderChatPane
              preset={preset}
              workspaceId={workspaceId}
              builderPrompt={builderPrompt}
            />
          </TabsContent>

          <TabsContent value="configuration" className="mt-0 h-full">
            <AgentPresetConfigurationPanel
              form={form}
              isSaving={isSaving}
              actionSuggestions={actionSuggestions}
              namespaceSuggestions={namespaceSuggestions}
              enabledModelOptions={enabledModelOptions}
              enabledModelsLoaded={enabledModelsLoaded}
              mcpIntegrations={mcpIntegrations}
              mcpIntegrationsIsLoading={mcpIntegrationsIsLoading}
              toolApprovalFields={toolApprovalFields}
              onAddToolApproval={onAddToolApproval}
              onRemoveToolApproval={onRemoveToolApproval}
            />
          </TabsContent>

          <TabsContent value="subagents" className="mt-0 h-full">
            <AgentPresetSubagentsPanel
              form={form}
              isSaving={isSaving}
              parentPreset={preset}
              agentPresets={agentPresets}
              subagentFields={subagentFields}
              onAddSubagent={onAddSubagent}
              onRemoveSubagent={onRemoveSubagent}
            />
          </TabsContent>

          <TabsContent value="skills" className="mt-0 h-full overflow-hidden">
            <AgentPresetSkillsPanel
              form={form}
              workspaceId={workspaceId}
              isSaving={isSaving}
              skillFields={skillFields}
              onAddSkillBinding={onAddSkillBinding}
              onRemoveSkillBinding={onRemoveSkillBinding}
            />
          </TabsContent>

          <TabsContent value="structured-output" className="mt-0 h-full">
            <AgentPresetStructuredOutputPanel form={form} isSaving={isSaving} />
          </TabsContent>

          <TabsContent value="versions" className="mt-0 h-full">
            <AgentPresetVersionsPanel
              workspaceId={workspaceId}
              preset={preset}
            />
          </TabsContent>

          {channelsEnabled ? (
            <TabsContent value="channels" className="mt-0 h-full">
              <SlackChannelPanel workspaceId={workspaceId} preset={preset} />
            </TabsContent>
          ) : null}
        </div>
      </Tabs>
    </div>
  )
}

function AgentPresetConfigurationPanel({
  form,
  isSaving,
  actionSuggestions,
  namespaceSuggestions,
  enabledModelOptions,
  enabledModelsLoaded,
  mcpIntegrations,
  mcpIntegrationsIsLoading,
  toolApprovalFields,
  onAddToolApproval,
  onRemoveToolApproval,
}: {
  form: UseFormReturn<AgentPresetFormValues>
  isSaving: boolean
  actionSuggestions: Suggestion[]
  namespaceSuggestions: Suggestion[]
  enabledModelOptions: EnabledModelOption[]
  enabledModelsLoaded: boolean
  mcpIntegrations: McpIntegrationOption[]
  mcpIntegrationsIsLoading: boolean
  toolApprovalFields: Array<{ id: string }>
  onAddToolApproval: () => void
  onRemoveToolApproval: (index: number) => void
}) {
  const sourceId = form.watch("source_id")
  const modelProvider = form.watch("model_provider")
  const modelName = form.watch("model_name")
  const baseUrl = form.watch("base_url")
  const thinkingEnabled = form.watch("enableThinking")
  const internetAccessEnabled = form.watch("enableInternetAccess")
  const selectedModel = useMemo(
    () =>
      findEnabledModelOption(enabledModelOptions, {
        sourceId,
        modelProvider,
        modelName,
        baseUrl,
      }),
    [baseUrl, enabledModelOptions, sourceId, modelName, modelProvider]
  )
  const hasMissingEnabledModel =
    enabledModelsLoaded && !selectedModel && Boolean(modelProvider || modelName)
  const legacyModelLabel =
    hasMissingEnabledModel && modelProvider && modelName
      ? `${modelProvider} / ${modelName}`
      : null
  const [isModelPickerOpen, setIsModelPickerOpen] = useState(false)

  return (
    <ScrollArea className="h-full">
      <div className="flex flex-col gap-8 px-6 py-6 pb-20 text-sm">
        <section className="space-y-4">
          <div className="grid gap-4">
            <FormField
              control={form.control}
              name="model_name"
              render={({ field }) => (
                <FormItem>
                  <FormLabel>Model</FormLabel>
                  <FormDescription>
                    Choose from the models available in this workspace.
                  </FormDescription>
                  <Popover
                    open={isModelPickerOpen}
                    onOpenChange={setIsModelPickerOpen}
                  >
                    <FormControl>
                      <PopoverTrigger asChild>
                        <button
                          type="button"
                          role="combobox"
                          aria-expanded={isModelPickerOpen}
                          className={cn(
                            "flex h-9 w-full items-center justify-between whitespace-nowrap rounded-md border border-input bg-transparent px-3 py-2 text-xs shadow-sm focus:outline-none focus-visible:ring-1 focus-visible:ring-inset focus-visible:ring-ring disabled:cursor-not-allowed disabled:opacity-50",
                            !selectedModel && "text-muted-foreground"
                          )}
                          disabled={
                            isSaving || enabledModelOptions.length === 0
                          }
                        >
                          {selectedModel ? (
                            <span className="flex min-w-0 items-center gap-2">
                              <ProviderIcon
                                providerId={selectedModel.iconId}
                                className="size-4 shrink-0 rounded-none bg-transparent p-0"
                              />
                              <span className="truncate">
                                {selectedModel.displayName}
                              </span>
                              <span className="shrink-0 text-muted-foreground">
                                {selectedModel.sourceName}
                              </span>
                            </span>
                          ) : legacyModelLabel ? (
                            <span className="flex min-w-0 items-center gap-2">
                              <ProviderIcon
                                providerId={getProviderIconId(modelProvider)}
                                className="size-4 shrink-0 rounded-none bg-transparent p-0"
                              />
                              <span className="truncate">
                                {legacyModelLabel}
                              </span>
                              <span className="shrink-0 text-muted-foreground">
                                Legacy
                              </span>
                            </span>
                          ) : enabledModelOptions.length ? (
                            "Select a model"
                          ) : (
                            "No enabled models"
                          )}
                          <ChevronsUpDown className="ml-2 size-4 shrink-0 opacity-50" />
                        </button>
                      </PopoverTrigger>
                    </FormControl>
                    <PopoverContent
                      align="start"
                      className="w-[--radix-popover-trigger-width] p-0"
                      sideOffset={4}
                    >
                      <Command
                        filter={(value, search) => {
                          const option = enabledModelOptions.find(
                            (o) =>
                              getModelSelectionKey({
                                source_id: o.sourceId,
                                model_provider: o.modelProvider,
                                model_name: o.modelName,
                              }) === value
                          )
                          if (!option) {
                            return 0
                          }
                          const haystack =
                            `${option.displayName} ${option.modelName} ${option.sourceName} ${option.modelProvider}`.toLowerCase()
                          return haystack.includes(search.toLowerCase()) ? 1 : 0
                        }}
                      >
                        <CommandInput placeholder="Search models..." />
                        <CommandList>
                          <CommandEmpty>
                            No models match the search.
                          </CommandEmpty>
                          <CommandGroup>
                            {enabledModelOptions.map((option) => {
                              const optionKey = getModelSelectionKey({
                                source_id: option.sourceId,
                                model_provider: option.modelProvider,
                                model_name: option.modelName,
                              })
                              const isSelected =
                                optionKey ===
                                (selectedModel
                                  ? getModelSelectionKey({
                                      source_id: selectedModel.sourceId,
                                      model_provider:
                                        selectedModel.modelProvider,
                                      model_name: selectedModel.modelName,
                                    })
                                  : null)
                              return (
                                <CommandItem
                                  key={optionKey}
                                  value={optionKey}
                                  onSelect={() => {
                                    field.onChange(option.modelName)
                                    syncFormModelSelection(form, option, true)
                                    setIsModelPickerOpen(false)
                                  }}
                                  className="flex items-center gap-2"
                                >
                                  <ProviderIcon
                                    providerId={option.iconId}
                                    className="size-4 shrink-0 rounded-none bg-transparent p-0"
                                  />
                                  <span className="min-w-0 truncate">
                                    {option.displayName}
                                  </span>
                                  <span className="shrink-0 text-[11px] text-muted-foreground">
                                    {option.sourceName}
                                  </span>
                                  <Check
                                    className={cn(
                                      "ml-auto size-4 shrink-0",
                                      isSelected ? "opacity-100" : "opacity-0"
                                    )}
                                  />
                                </CommandItem>
                              )
                            })}
                          </CommandGroup>
                        </CommandList>
                      </Command>
                    </PopoverContent>
                  </Popover>
                </FormItem>
              )}
            />
          </div>
          <div className="grid gap-4 md:grid-cols-[minmax(0,1fr)_180px]">
            <FormField
              control={form.control}
              name="retries"
              render={({ field }) => (
                <FormItem>
                  <FormLabel>Retries</FormLabel>
                  <FormControl>
                    <Input
                      type="number"
                      min={0}
                      {...field}
                      disabled={isSaving}
                    />
                  </FormControl>
                </FormItem>
              )}
            />
          </div>
          <div className="overflow-hidden rounded-lg border">
            <div className="flex items-start justify-between gap-4 px-4 py-3">
              <div className="space-y-1">
                <label
                  htmlFor="enable-thinking"
                  className="text-sm font-medium leading-none"
                >
                  Thinking
                </label>
                <p className="text-xs text-muted-foreground">
                  Adds higher reasoning effort by default.
                </p>
              </div>
              <Switch
                id="enable-thinking"
                checked={thinkingEnabled}
                onCheckedChange={(checked) =>
                  form.setValue("enableThinking", checked, {
                    shouldDirty: true,
                  })
                }
                disabled={isSaving}
              />
            </div>
            <div className="border-t" />
            <div className="flex items-start justify-between gap-4 px-4 py-3">
              <div className="space-y-1">
                <div className="flex items-center gap-1.5">
                  <label
                    htmlFor="enable-internet-access"
                    className="text-sm font-medium leading-none"
                  >
                    Internet access
                  </label>
                  <Tooltip>
                    <TooltipTrigger asChild>
                      <button
                        type="button"
                        className="text-muted-foreground transition-colors hover:text-foreground"
                      >
                        <AlertCircle className="size-4" />
                      </button>
                    </TooltipTrigger>
                    <TooltipContent>
                      Required for in-process MCP servers.
                    </TooltipContent>
                  </Tooltip>
                </div>
                <p className="text-xs text-muted-foreground">
                  Allows the agent to reach the web from the sandbox when tools
                  need it.
                </p>
              </div>
              <Switch
                id="enable-internet-access"
                checked={internetAccessEnabled}
                onCheckedChange={(checked) =>
                  form.setValue("enableInternetAccess", checked, {
                    shouldDirty: true,
                  })
                }
                disabled={isSaving}
              />
            </div>
          </div>
        </section>

        <Separator />

        <section className="space-y-4">
          <FormField
            control={form.control}
            name="actions"
            render={({ field }) => (
              <FormItem>
                <FormLabel>Allowed tools</FormLabel>
                <FormControl>
                  <MultiTagCommandInput
                    value={field.value}
                    onChange={field.onChange}
                    suggestions={actionSuggestions}
                    placeholder="+ Add tool"
                    searchKeys={["label", "value", "description", "group"]}
                    allowCustomTags
                    disabled={isSaving}
                  />
                </FormControl>
              </FormItem>
            )}
          />
          <FormField
            control={form.control}
            name="mcpIntegrations"
            render={({ field }) => (
              <FormItem>
                <FormLabel>Allowed MCP integrations</FormLabel>
                <FormControl>
                  <MultiTagCommandInput
                    value={field.value ?? []}
                    onChange={(next) => field.onChange(next)}
                    searchKeys={["label", "value"]}
                    suggestions={(mcpIntegrations ?? []).map((integration) => ({
                      id: integration.id,
                      label: integration.name,
                      value: integration.id,
                      description: integration.description || "MCP Integration",
                      icon: (
                        <ProviderIcon
                          providerId={integration.providerId || "custom"}
                          className="size-3 bg-transparent p-0 mx-1"
                        />
                      ),
                    }))}
                    placeholder={
                      mcpIntegrationsIsLoading
                        ? "Loading integrations..."
                        : "Select MCP integrations"
                    }
                    disabled={isSaving}
                  />
                </FormControl>
              </FormItem>
            )}
          />
          <FormField
            control={form.control}
            name="namespaces"
            render={({ field }) => (
              <FormItem>
                <FormLabel>Tool namespaces</FormLabel>
                <FormControl>
                  <MultiTagCommandInput
                    value={field.value}
                    onChange={field.onChange}
                    suggestions={namespaceSuggestions}
                    placeholder="Restrict to namespaces (optional)"
                    searchKeys={["label", "value"]}
                    allowCustomTags
                    disabled={isSaving}
                  />
                </FormControl>
              </FormItem>
            )}
          />
        </section>

        <Separator />

        <section className="space-y-4">
          <div className="flex items-center justify-between">
            <p className="text-sm font-medium">Approval rules</p>
            <Button
              type="button"
              size="sm"
              variant="outline"
              onClick={onAddToolApproval}
              disabled={isSaving}
            >
              <Plus className="mr-2 size-4" />
              Add rule
            </Button>
          </div>
          {toolApprovalFields.length === 0 ? (
            <p className="rounded-md border border-dashed px-3 py-4 text-xs text-muted-foreground">
              No manual approval rules yet. Add a tool to require human review
              or to force manual overrides.
            </p>
          ) : (
            <div className="space-y-3">
              <div className="grid gap-3 px-3 md:grid-cols-[minmax(0,1fr)_220px_auto] md:items-center">
                <div className="text-xs font-medium uppercase text-muted-foreground">
                  Tool
                </div>
                <div className="text-xs font-medium uppercase text-muted-foreground md:text-center">
                  Manual approval
                </div>
                <div className="w-10" aria-hidden="true" />
              </div>

              <div className="space-y-2">
                {toolApprovalFields.map((item, index) => {
                  const approvalSwitchId = `tool-approval-${item.id}-allow`

                  return (
                    <div
                      key={item.id}
                      className="grid gap-3 px-3 py-3 md:grid-cols-[minmax(0,1fr)_220px_auto] md:items-center"
                    >
                      <FormField
                        control={form.control}
                        name={`toolApprovals.${index}.tool`}
                        render={({ field }) => (
                          <FormItem className="flex-1">
                            <FormControl>
                              <ActionSelect
                                field={field}
                                suggestions={[...actionSuggestions]}
                                searchKeys={[
                                  "label",
                                  "value",
                                  "description",
                                  "group",
                                ]}
                                placeholder="Select an action or MCP tool..."
                                disabled={isSaving}
                              />
                            </FormControl>
                          </FormItem>
                        )}
                      />
                      <FormField
                        control={form.control}
                        name={`toolApprovals.${index}.allow`}
                        render={({ field }) => (
                          <FormItem className="md:justify-self-center">
                            <FormControl>
                              <div className="flex items-center gap-3 px-3 py-2">
                                <Switch
                                  id={approvalSwitchId}
                                  checked={Boolean(field.value)}
                                  onCheckedChange={field.onChange}
                                  disabled={isSaving}
                                />
                                <span className="text-sm font-medium min-w-[100px]">
                                  {field.value ? "Required" : "Not required"}
                                </span>
                              </div>
                            </FormControl>
                          </FormItem>
                        )}
                      />
                      <Button
                        type="button"
                        variant="ghost"
                        size="icon"
                        className="justify-self-start self-start text-muted-foreground md:justify-self-end"
                        onClick={() => onRemoveToolApproval(index)}
                        disabled={isSaving}
                        aria-label="Remove approval rule"
                      >
                        <Trash2 className="size-4" />
                      </Button>
                    </div>
                  )
                })}
              </div>
            </div>
          )}
        </section>
      </div>
    </ScrollArea>
  )
}

function AgentPresetSubagentsPanel({
  form,
  isSaving,
  parentPreset,
  agentPresets,
  subagentFields,
  onAddSubagent,
  onRemoveSubagent,
}: {
  form: UseFormReturn<AgentPresetFormValues>
  isSaving: boolean
  parentPreset: AgentPresetRead | null
  agentPresets: AgentPresetReadMinimal[]
  subagentFields: Array<{ id: string }>
  onAddSubagent: () => void
  onRemoveSubagent: (index: number) => void
}) {
  const agentsEnabled = form.watch("agentsEnabled")
  const presetOptions = useMemo(
    () =>
      agentPresets
        .filter((preset) => preset.id !== parentPreset?.id)
        .sort((a, b) => a.name.localeCompare(b.name)),
    [agentPresets, parentPreset?.id]
  )

  return (
    <ScrollArea className="h-full">
      <div className="flex flex-col gap-8 px-6 py-6 pb-20 text-sm">
        <section className="space-y-4">
          <div className="overflow-hidden rounded-lg border">
            <div className="flex items-start justify-between gap-4 px-4 py-3">
              <div className="space-y-1">
                <label
                  htmlFor="enable-subagents"
                  className="text-sm font-medium leading-none"
                >
                  Agent tool
                </label>
                <p className="text-xs text-muted-foreground">
                  Adds Claude's Agent tool. Dynamic subagents are enabled by
                  default and inherit this agent's tools, MCP integrations,
                  approvals, and sandbox policy.
                </p>
              </div>
              <Switch
                id="enable-subagents"
                checked={agentsEnabled}
                onCheckedChange={(checked) =>
                  form.setValue("agentsEnabled", checked, {
                    shouldDirty: true,
                    shouldValidate: true,
                  })
                }
                disabled={isSaving}
              />
            </div>
          </div>
        </section>

        <Separator />

        <section className="space-y-4">
          <div className="flex items-center justify-between gap-4">
            <div className="space-y-1">
              <p className="text-sm font-medium">Preset subagents</p>
              <p className="text-xs text-muted-foreground">
                Attach named preset agents that the parent can invoke with
                explicit descriptions and turn limits.
              </p>
            </div>
            <Button
              type="button"
              size="sm"
              variant="outline"
              onClick={onAddSubagent}
              disabled={
                isSaving || !agentsEnabled || presetOptions.length === 0
              }
            >
              <Plus className="mr-2 size-4" />
              Add preset
            </Button>
          </div>

          {!agentsEnabled ? (
            <p className="rounded-md border border-dashed px-3 py-4 text-xs text-muted-foreground">
              Enable the Agent tool to allow dynamic subagents or attach preset
              subagents.
            </p>
          ) : presetOptions.length === 0 ? (
            <p className="rounded-md border border-dashed px-3 py-4 text-xs text-muted-foreground">
              Create another agent preset first, then attach it here. Dynamic
              subagents are already available while the Agent tool is enabled.
            </p>
          ) : subagentFields.length === 0 ? (
            <p className="rounded-md border border-dashed px-3 py-4 text-xs text-muted-foreground">
              No preset subagents attached. Dynamic subagents can still run and
              inherit this agent's current scopes.
            </p>
          ) : (
            <div className="space-y-4">
              {subagentFields.map((item, index) => {
                const selectedPreset = form.watch(`subagents.${index}.preset`)
                const selectedPresetIsMissing =
                  selectedPreset.length > 0 &&
                  !presetOptions.some(
                    (preset) => preset.slug === selectedPreset
                  )

                return (
                  <div
                    key={item.id}
                    className="space-y-4 rounded-lg border px-4 py-4"
                  >
                    <div className="flex items-start justify-between gap-4">
                      <div className="min-w-0 flex-1 space-y-4">
                        <FormField
                          control={form.control}
                          name={`subagents.${index}.preset`}
                          render={({ field }) => (
                            <FormItem>
                              <FormLabel>Preset</FormLabel>
                              <Select
                                value={field.value}
                                onValueChange={field.onChange}
                                disabled={isSaving || !agentsEnabled}
                              >
                                <FormControl>
                                  <SelectTrigger>
                                    <SelectValue placeholder="Select preset" />
                                  </SelectTrigger>
                                </FormControl>
                                <SelectContent>
                                  {selectedPresetIsMissing ? (
                                    <SelectItem value={selectedPreset}>
                                      {selectedPreset} unavailable
                                    </SelectItem>
                                  ) : null}
                                  {presetOptions.map((preset) => {
                                    const unavailableReason =
                                      getSubagentPresetUnavailableReason(preset)
                                    const optionLabel = (
                                      <span className="flex min-w-0 items-center gap-2">
                                        <span className="min-w-0 truncate">
                                          {preset.name}
                                        </span>
                                        <span className="min-w-0 truncate text-xs text-muted-foreground">
                                          {preset.slug}
                                        </span>
                                        {unavailableReason ? (
                                          <span className="ml-auto shrink-0 rounded border border-border px-1.5 py-0.5 text-[10px] uppercase text-muted-foreground">
                                            Approvals
                                          </span>
                                        ) : null}
                                      </span>
                                    )

                                    if (unavailableReason) {
                                      return (
                                        <Tooltip key={preset.id}>
                                          <TooltipTrigger asChild>
                                            <SelectItem
                                              value={preset.slug}
                                              disabled
                                              className="data-[disabled]:pointer-events-auto data-[disabled]:opacity-60"
                                            >
                                              {optionLabel}
                                            </SelectItem>
                                          </TooltipTrigger>
                                          <TooltipContent
                                            side="right"
                                            className="max-w-xs"
                                          >
                                            {unavailableReason}
                                          </TooltipContent>
                                        </Tooltip>
                                      )
                                    }

                                    return (
                                      <SelectItem
                                        key={preset.id}
                                        value={preset.slug}
                                      >
                                        {optionLabel}
                                      </SelectItem>
                                    )
                                  })}
                                </SelectContent>
                              </Select>
                              <FormDescription>
                                The preset slug is stored in the agent binding.
                              </FormDescription>
                              <FormMessage />
                            </FormItem>
                          )}
                        />

                        <div className="grid gap-4 md:grid-cols-2">
                          <FormField
                            control={form.control}
                            name={`subagents.${index}.name`}
                            render={({ field }) => (
                              <FormItem>
                                <FormLabel>Alias</FormLabel>
                                <FormControl>
                                  <Input
                                    placeholder="triage-analyst"
                                    value={field.value ?? ""}
                                    onChange={field.onChange}
                                    disabled={isSaving || !agentsEnabled}
                                  />
                                </FormControl>
                                <FormDescription>
                                  Optional. Defaults to the preset slug.
                                </FormDescription>
                                <FormMessage />
                              </FormItem>
                            )}
                          />
                          <FormField
                            control={form.control}
                            name={`subagents.${index}.presetVersion`}
                            render={({ field }) => (
                              <FormItem>
                                <FormLabel>Version</FormLabel>
                                <FormControl>
                                  <Input
                                    type="number"
                                    min={1}
                                    placeholder="Current"
                                    value={field.value ?? ""}
                                    onChange={field.onChange}
                                    disabled={isSaving || !agentsEnabled}
                                  />
                                </FormControl>
                                <FormDescription>
                                  Optional. Blank uses the current version.
                                </FormDescription>
                                <FormMessage />
                              </FormItem>
                            )}
                          />
                        </div>

                        <FormField
                          control={form.control}
                          name={`subagents.${index}.description`}
                          render={({ field }) => (
                            <FormItem>
                              <FormLabel>Description</FormLabel>
                              <FormControl>
                                <Textarea
                                  placeholder="When should the parent delegate to this subagent?"
                                  value={field.value ?? ""}
                                  onChange={field.onChange}
                                  disabled={isSaving || !agentsEnabled}
                                />
                              </FormControl>
                              <FormDescription>
                                Used to decide when to call the subagent.
                              </FormDescription>
                              <FormMessage />
                            </FormItem>
                          )}
                        />

                        <FormField
                          control={form.control}
                          name={`subagents.${index}.maxTurns`}
                          render={({ field }) => (
                            <FormItem className="max-w-[220px]">
                              <FormLabel>Max turns</FormLabel>
                              <FormControl>
                                <Input
                                  type="number"
                                  min={1}
                                  placeholder="No limit"
                                  value={field.value ?? ""}
                                  onChange={field.onChange}
                                  disabled={isSaving || !agentsEnabled}
                                />
                              </FormControl>
                              <FormMessage />
                            </FormItem>
                          )}
                        />
                      </div>
                      <Button
                        type="button"
                        variant="ghost"
                        size="icon"
                        className="shrink-0 text-muted-foreground"
                        onClick={() => onRemoveSubagent(index)}
                        disabled={isSaving}
                        aria-label="Remove subagent"
                      >
                        <Trash2 className="size-4" />
                      </Button>
                    </div>
                  </div>
                )
              })}
            </div>
          )}
        </section>
      </div>
    </ScrollArea>
  )
}

function AgentPresetSkillsPanel({
  form,
  workspaceId,
  isSaving,
  skillFields,
  onAddSkillBinding,
  onRemoveSkillBinding,
}: {
  form: UseFormReturn<AgentPresetFormValues>
  workspaceId: string
  isSaving: boolean
  skillFields: Array<{ id: string }>
  onAddSkillBinding: (binding: SkillBindingFormValue) => void
  onRemoveSkillBinding: (index: number) => void
}) {
  const [isPickerOpen, setIsPickerOpen] = useState(false)
  const selectedSkills = form.watch("skills")
  const { skills, skillsLoading, skillsError } = useSkills(workspaceId)
  const attachedSkillIds = useMemo(
    () => new Set((selectedSkills ?? []).map((binding) => binding.skillId)),
    [selectedSkills]
  )
  const availableSkillsToAdd = useMemo(
    () =>
      (skills ?? []).filter((skill) => {
        return !!skill.current_version_id && !attachedSkillIds.has(skill.id)
      }),
    [attachedSkillIds, skills]
  )
  const hasUnattachedSkills = useMemo(
    () => (skills ?? []).some((skill) => !attachedSkillIds.has(skill.id)),
    [attachedSkillIds, skills]
  )

  function handleAddSkill(skillId: string) {
    onAddSkillBinding({
      skillId,
      skillVersionId: "",
    })
    setIsPickerOpen(false)
  }

  return (
    <div className="h-full overflow-auto">
      <div className="flex min-w-0 w-full flex-col gap-8 px-6 py-6 pb-20 text-sm">
        <section className="min-w-0 w-full space-y-4">
          <div className="flex items-center justify-between gap-3">
            <div className="space-y-1">
              <p className="text-sm font-medium">Attached skills</p>
              <p className="text-xs text-muted-foreground">
                Pin published skill versions to this preset.
              </p>
            </div>
            <Button
              type="button"
              size="sm"
              variant="outline"
              onClick={() => setIsPickerOpen(true)}
              disabled={
                isSaving || skillsLoading || availableSkillsToAdd.length === 0
              }
            >
              <Plus className="mr-2 size-4" />
              Add skill
            </Button>
          </div>
          {skillsError ? (
            <Alert variant="destructive">
              <AlertCircle className="size-4" />
              <AlertTitle>Unable to load skills</AlertTitle>
              <AlertDescription>
                {getApiErrorDetail(skillsError) ?? "Please try again."}
              </AlertDescription>
            </Alert>
          ) : skillsLoading ? (
            <div className="flex items-center gap-2 rounded-md border px-3 py-4 text-xs text-muted-foreground">
              <Loader2 className="size-4 animate-spin" />
              Loading skills...
            </div>
          ) : skillFields.length === 0 ? (
            <p className="rounded-md border border-dashed px-3 py-4 text-xs text-muted-foreground">
              No skills attached yet.
            </p>
          ) : (
            <div className="min-w-0 w-full space-y-3">
              {skillFields.map((item, index) => (
                <AgentPresetSkillBindingRow
                  key={item.id}
                  form={form}
                  workspaceId={workspaceId}
                  index={index}
                  isSaving={isSaving}
                  availableSkills={skills ?? []}
                  onRemove={onRemoveSkillBinding}
                />
              ))}
            </div>
          )}
          {!skillsLoading &&
          !skillsError &&
          skillFields.length > 0 &&
          availableSkillsToAdd.length === 0 ? (
            <p className="text-xs text-muted-foreground">
              {hasUnattachedSkills
                ? "Only skills with published versions can be attached."
                : "All workspace skills are already attached to this preset."}
            </p>
          ) : null}
        </section>
      </div>
      <CommandDialog open={isPickerOpen} onOpenChange={setIsPickerOpen}>
        <CommandInput placeholder="Search skills..." />
        <CommandList>
          <CommandEmpty>No skills found.</CommandEmpty>
          <CommandGroup heading="Workspace skills">
            {availableSkillsToAdd.map((skill) => (
              <CommandItem
                key={skill.id}
                value={`${skill.name} ${skill.description ?? ""}`}
                onSelect={() => handleAddSkill(skill.id)}
              >
                <div className="flex min-w-0 flex-col gap-0.5">
                  <span>{skill.name}</span>
                  <span className="truncate text-xs text-muted-foreground">
                    {skill.description?.trim() || skill.name}
                  </span>
                </div>
              </CommandItem>
            ))}
          </CommandGroup>
        </CommandList>
      </CommandDialog>
    </div>
  )
}

function AgentPresetSkillBindingRow({
  form,
  workspaceId,
  index,
  isSaving,
  availableSkills,
  onRemove,
}: {
  form: UseFormReturn<AgentPresetFormValues>
  workspaceId: string
  index: number
  isSaving: boolean
  availableSkills: SkillReadMinimal[]
  onRemove: (index: number) => void
}) {
  const skillFieldName = `skills.${index}.skillId` as const
  const versionFieldName = `skills.${index}.skillVersionId` as const
  const selectedSkillId = form.watch(skillFieldName)
  const selectedVersionId = form.watch(versionFieldName)
  const selectedSkill = availableSkills.find(
    (skill) => skill.id === selectedSkillId
  )
  const { versions, versionsLoading, versionsError } = useSkillVersions(
    workspaceId,
    selectedSkillId || null
  )
  const versionOptions = useMemo(
    () => [...(versions ?? [])].sort((a, b) => b.version - a.version),
    [versions]
  )

  useEffect(() => {
    if (!selectedSkillId) {
      if (selectedVersionId) {
        form.setValue(versionFieldName, "", { shouldDirty: true })
      }
      return
    }

    if (versionsLoading || versionOptions.length === 0) {
      return
    }

    const hasSelectedVersion = versionOptions.some(
      (version) => version.id === selectedVersionId
    )
    if (hasSelectedVersion) {
      return
    }

    const preferredVersionId =
      selectedSkill?.current_version_id ?? versionOptions[0]?.id ?? ""
    if (!preferredVersionId) {
      return
    }

    form.setValue(versionFieldName, preferredVersionId, { shouldDirty: true })
  }, [
    form,
    selectedSkill?.current_version_id,
    selectedSkillId,
    selectedVersionId,
    versionFieldName,
    versionOptions,
    versionsLoading,
  ])

  const selectedVersion = versionOptions.find((v) => v.id === selectedVersionId)
  const [isDescriptionExpanded, setIsDescriptionExpanded] = useState(false)
  const skillHref = selectedSkillId
    ? `/workspaces/${workspaceId}/skills/${selectedSkillId}`
    : null
  const displaySkillName = selectedVersion?.name?.trim() || selectedSkill?.name
  const displaySkillDescription =
    selectedVersion?.description?.trim() ||
    selectedSkill?.description?.trim() ||
    null
  let versionPlaceholder = "Version"
  if (versionsLoading) {
    versionPlaceholder = "..."
  } else if (versionOptions.length === 0) {
    versionPlaceholder = "No versions"
  }
  const selectedVersionLabel = selectedVersion
    ? `v${selectedVersion.version}`
    : versionPlaceholder

  function handleOpenSkill() {
    if (!skillHref) {
      return
    }
    window.open(skillHref, "_blank", "noopener,noreferrer")
  }

  return (
    <div className="flex min-w-0 items-start gap-3 rounded-md border px-3 py-2.5">
      <Pyramid className="mt-0.5 size-4 shrink-0 text-muted-foreground" />
      <div className="min-w-0 flex-1 space-y-0.5">
        <div className="flex min-w-0 items-center gap-2">
          {skillHref ? (
            <button
              type="button"
              className="min-w-0 truncate rounded-sm text-left text-sm font-medium underline-offset-2 hover:underline focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-ring"
              onClick={handleOpenSkill}
            >
              {displaySkillName ?? "Unknown skill"}
            </button>
          ) : (
            <span className="truncate text-sm font-medium">
              {displaySkillName ?? "Unknown skill"}
            </span>
          )}
        </div>
        {displaySkillDescription ? (
          <div className="space-y-1">
            <p
              className={
                isDescriptionExpanded
                  ? "text-xs text-muted-foreground"
                  : "line-clamp-2 text-xs text-muted-foreground"
              }
            >
              {displaySkillDescription}
            </p>
            <button
              type="button"
              className="text-xs text-muted-foreground underline-offset-2 hover:underline"
              onClick={(event) => {
                event.stopPropagation()
                setIsDescriptionExpanded((value) => !value)
              }}
            >
              {isDescriptionExpanded ? "Show less" : "Show more"}
            </button>
          </div>
        ) : null}
        {selectedSkillId && versionsError ? (
          <p className="text-xs text-destructive">
            {getApiErrorDetail(versionsError) ?? "Failed to load versions."}
          </p>
        ) : null}
        {selectedSkillId && !versionsLoading && versionOptions.length === 0 ? (
          <p className="text-xs text-muted-foreground">
            No published versions yet.
          </p>
        ) : null}
      </div>
      <FormField
        control={form.control}
        name={versionFieldName}
        render={({ field }) => (
          <FormItem className="shrink-0">
            <Select
              value={field.value || ""}
              onValueChange={field.onChange}
              disabled={
                isSaving ||
                !selectedSkillId ||
                versionsLoading ||
                versionOptions.length === 0
              }
            >
              <FormControl>
                <SelectTrigger className="h-7 w-auto gap-1.5 border-none bg-muted/50 px-2 text-xs shadow-none">
                  <span aria-hidden>{selectedVersionLabel}</span>
                  <SelectValue
                    className="sr-only"
                    placeholder={versionPlaceholder}
                  />
                </SelectTrigger>
              </FormControl>
              <SelectContent>
                {versionOptions.map((version) => (
                  <SelectItem key={version.id} value={version.id}>
                    {formatSkillVersionLabel(version)}
                  </SelectItem>
                ))}
              </SelectContent>
            </Select>
            <FormMessage />
          </FormItem>
        )}
      />
      <Button
        type="button"
        variant="ghost"
        size="icon"
        className="size-7 shrink-0 text-muted-foreground"
        onClick={() => onRemove(index)}
        disabled={isSaving}
        aria-label="Remove attached skill"
      >
        <Trash2 className="size-3.5" />
      </Button>
    </div>
  )
}

function AgentPresetStructuredOutputPanel({
  form,
  isSaving,
}: {
  form: UseFormReturn<AgentPresetFormValues>
  isSaving: boolean
}) {
  const outputTypeKind = form.watch("outputTypeKind")

  return (
    <ScrollArea className="h-full">
      <div className="flex flex-col gap-8 px-6 py-6 pb-20 text-sm">
        <section className="space-y-4">
          <div className="grid gap-4 md:grid-cols-2">
            <FormField
              control={form.control}
              name="outputTypeKind"
              render={({ field }) => (
                <FormItem>
                  <FormLabel>Output type</FormLabel>
                  <FormControl>
                    <Select
                      value={field.value}
                      onValueChange={field.onChange}
                      disabled={isSaving}
                    >
                      <SelectTrigger>
                        <SelectValue />
                      </SelectTrigger>
                      <SelectContent>
                        <SelectItem value="none">
                          <div className="flex items-center gap-2">
                            <Type className="size-4" />
                            <span>Text only</span>
                          </div>
                        </SelectItem>
                        <SelectItem value="data-type">
                          <div className="flex items-center gap-2">
                            <Box className="size-4" />
                            <span>Structured</span>
                          </div>
                        </SelectItem>
                        <SelectItem value="json">
                          <div className="flex items-center gap-2">
                            <Braces className="size-4" />
                            <span>JSON schema</span>
                          </div>
                        </SelectItem>
                      </SelectContent>
                    </Select>
                  </FormControl>
                </FormItem>
              )}
            />
            {outputTypeKind === "data-type" ? (
              <FormField
                control={form.control}
                name="outputTypeDataType"
                render={({ field }) => (
                  <FormItem>
                    <FormLabel>Data type</FormLabel>
                    <FormControl>
                      <Select
                        value={field.value ?? ""}
                        onValueChange={field.onChange}
                        disabled={isSaving}
                      >
                        <SelectTrigger>
                          <SelectValue placeholder="Select type" />
                        </SelectTrigger>
                        <SelectContent>
                          {DATA_TYPE_OUTPUT_TYPES.map((option) => {
                            const Icon = option.icon
                            return (
                              <SelectItem
                                key={option.value}
                                value={option.value}
                              >
                                <div className="flex items-center gap-2">
                                  <Icon className="size-4" />
                                  <span>{option.label}</span>
                                </div>
                              </SelectItem>
                            )
                          })}
                        </SelectContent>
                      </Select>
                    </FormControl>
                  </FormItem>
                )}
              />
            ) : null}
          </div>
          {outputTypeKind === "json" ? (
            <FormField
              control={form.control}
              name="outputTypeJson"
              render={({ field }) => (
                <FormItem>
                  <FormLabel>JSON schema</FormLabel>
                  <FormDescription>
                    Define a JSON schema for structured output.
                  </FormDescription>
                  <FormControl>
                    <CodeEditor
                      value={field.value ?? ""}
                      onChange={(value) => field.onChange(value)}
                      language="json"
                      readOnly={isSaving}
                      className="min-h-[200px]"
                    />
                  </FormControl>
                </FormItem>
              )}
            />
          ) : null}
        </section>
      </div>
    </ScrollArea>
  )
}

function AgentPresetBuilderChatPane({
  preset,
  workspaceId,
  builderPrompt,
}: {
  preset: AgentPresetRead | null
  workspaceId: string
  builderPrompt?: string
}) {
  const presetId = preset?.id
  const [selectedChatId, setSelectedChatId] = useState<string | null>(null)
  const [pendingBuilderPrompt, setPendingBuilderPrompt] = useState<
    string | undefined
  >(builderPrompt?.trim() ? builderPrompt.trim() : undefined)

  const {
    ready: chatReady,
    loading: chatReadyLoading,
    reason: chatReadyReason,
    modelInfo,
  } = useChatReadiness()

  const { chats, chatsLoading, chatsError, refetchChats } = useListChats(
    {
      workspaceId,
      entityType: "agent_preset_builder",
      entityId: presetId ?? undefined,
    },
    { enabled: Boolean(presetId && workspaceId) }
  )

  useEffect(() => {
    setSelectedChatId(null)
  }, [presetId])

  useEffect(() => {
    setPendingBuilderPrompt(
      builderPrompt?.trim() ? builderPrompt.trim() : undefined
    )
  }, [builderPrompt, presetId])

  const latestChatId = chats?.[0]?.id
  const activeChatId = selectedChatId ?? latestChatId

  const { createChat, createChatPending } = useCreateChat(workspaceId)
  const { chat, chatLoading, chatError } = useGetChatVercel({
    chatId: activeChatId,
    workspaceId,
  })

  const canStartChat = Boolean(presetId && chatReady && modelInfo)
  const shouldAutoCreateChat =
    canStartChat && !activeChatId && !chatsLoading && !createChatPending

  const handleCreateChat = async () => {
    if (!preset || !presetId || createChatPending || !chatReady || !modelInfo) {
      return
    }

    try {
      const newChat = await createChat({
        title: `${preset.name} builder assistant`,
        entity_type: "agent_preset_builder",
        entity_id: presetId,
      })
      setSelectedChatId(newChat.id)
      await refetchChats()
    } catch (error) {
      console.error("Failed to create builder assistant chat", error)
    }
  }

  // Auto-create chat when preset is ready and no chat exists
  useEffect(() => {
    if (shouldAutoCreateChat) {
      void handleCreateChat()
    }
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [shouldAutoCreateChat])

  const renderBody = () => {
    if (!preset || !presetId) {
      return (
        <div className="flex h-full items-center justify-center px-4">
          <Empty>
            <EmptyHeader>
              <EmptyMedia variant="icon">
                <Sparkles />
              </EmptyMedia>
              <EmptyTitle>Builder assistant</EmptyTitle>
              <EmptyDescription>
                Save the agent to start working with the assistant.
              </EmptyDescription>
            </EmptyHeader>
          </Empty>
        </div>
      )
    }

    if (chatReadyLoading) {
      return (
        <div className="flex h-full items-center justify-center">
          <CenteredSpinner />
        </div>
      )
    }

    if (!chatReady || !modelInfo) {
      return (
        <div className="flex h-full flex-col items-center justify-center gap-2 px-4 text-center text-xs text-muted-foreground">
          <AlertCircle className="size-5 text-amber-500" />
          <p>
            {chatReadyReason === "no_model"
              ? "Select a default model in organization agent settings to enable the builder assistant."
              : `Configure ${modelInfo?.provider ?? "your model provider"} credentials in organization agent settings to enable the builder assistant.`}
          </p>
          <Link
            href="/organization/settings/agent"
            className="text-xs font-medium text-primary hover:underline"
            target="_blank"
            rel="noopener noreferrer"
          >
            Go to agent settings
          </Link>
        </div>
      )
    }

    if (chatsError) {
      return (
        <div className="flex h-full items-center justify-center px-4">
          <Alert variant="destructive" className="w-full text-xs">
            <AlertTitle>Unable to load assistant chat</AlertTitle>
            <AlertDescription>
              {typeof chatsError.message === "string"
                ? chatsError.message
                : "Something went wrong while fetching the builder chat."}
            </AlertDescription>
          </Alert>
        </div>
      )
    }

    if (!activeChatId) {
      return (
        <div className="flex h-full items-center justify-center px-4">
          <Empty>
            <EmptyHeader>
              <EmptyMedia variant="icon">
                <MessageCircle />
              </EmptyMedia>
              <EmptyTitle>Builder assistant</EmptyTitle>
              <EmptyDescription>
                Save the preset name and choose an enabled model to activate the
                builder assistant.
              </EmptyDescription>
            </EmptyHeader>
          </Empty>
        </div>
      )
    }

    if (chatLoading || chatsLoading || !chat || !modelInfo) {
      return (
        <div className="flex h-full items-center justify-center">
          <CenteredSpinner />
        </div>
      )
    }

    if (chatError) {
      return (
        <div className="flex h-full items-center justify-center px-4">
          <Alert variant="destructive" className="w-full text-xs">
            <AlertTitle>Assistant unavailable</AlertTitle>
            <AlertDescription>
              {typeof chatError.message === "string"
                ? chatError.message
                : "We couldn't load the builder assistant."}
            </AlertDescription>
          </Alert>
        </div>
      )
    }

    return (
      <ChatSessionPane
        chat={chat}
        workspaceId={workspaceId}
        entityType="agent_preset_builder"
        entityId={presetId}
        className="flex-1 min-h-0"
        placeholder={`Talk to the builder assistant about your agent's prompt, tools, and approval rules...`}
        modelInfo={modelInfo}
        toolsEnabled={false}
        pendingMessage={pendingBuilderPrompt}
        onPendingMessageSent={() => setPendingBuilderPrompt(undefined)}
      />
    )
  }

  return (
    <div className="flex h-full flex-col bg-background">
      <div className="flex h-10 items-center justify-end border-b px-3">
        {preset ? (
          <div className="flex items-center gap-1">
            <ChatHistoryDropdown
              chats={chats}
              isLoading={chatsLoading}
              error={chatsError}
              selectedChatId={activeChatId ?? undefined}
              onSelectChat={(chatId) => setSelectedChatId(chatId)}
              align="end"
            />
            <Button
              size="sm"
              variant="ghost"
              className="text-xs"
              disabled={createChatPending || !canStartChat}
              onClick={() => void handleCreateChat()}
            >
              {createChatPending ? (
                <Loader2 className="mr-2 size-4 animate-spin" />
              ) : (
                <Plus className="mr-2 size-4" />
              )}
              New chat
            </Button>
          </div>
        ) : null}
      </div>
      <div className="flex-1 min-h-0">{renderBody()}</div>
    </div>
  )
}

function presetToFormValues(preset: AgentPresetRead): AgentPresetFormValues {
  const outputType =
    preset.output_type === null || preset.output_type === undefined
      ? null
      : preset.output_type
  const agents = preset.agents
  const agentsEnabled = agents?.enabled === true
  const subagents = agentsEnabled
    ? (agents.subagents ?? []).map((subagent) => ({
        preset: subagent.preset,
        name: subagent.name ?? "",
        description: subagent.description ?? "",
        presetVersion:
          subagent.preset_version === null ||
          subagent.preset_version === undefined
            ? ""
            : String(subagent.preset_version),
        maxTurns:
          subagent.max_turns === null || subagent.max_turns === undefined
            ? ""
            : String(subagent.max_turns),
      }))
    : []

  return {
    name: preset.name,
    slug: preset.slug,
    description: preset.description ?? "",
    instructions: preset.instructions ?? "",
    source_id: "",
    catalog_id: preset.catalog_id ?? "",
    model_provider: preset.model_provider,
    model_name: preset.model_name,
    base_url: preset.base_url ?? "",
    outputTypeKind: outputType
      ? typeof outputType === "string"
        ? "data-type"
        : "json"
      : "none",
    outputTypeDataType: typeof outputType === "string" ? outputType : "",
    outputTypeJson:
      outputType && typeof outputType === "object"
        ? JSON.stringify(outputType, null, 2)
        : "",
    actions: preset.actions ?? [],
    namespaces: preset.namespaces ?? [],
    toolApprovals: preset.tool_approvals
      ? Object.entries(preset.tool_approvals).map(
          ([tool, allow]): ToolApprovalFormValue => ({
            tool,
            allow: Boolean(allow),
          })
        )
      : [],
    mcpIntegrations: preset.mcp_integrations ?? [],
    agentsEnabled,
    subagents,
    skills:
      preset.skills?.map(
        (binding): SkillBindingFormValue => ({
          skillId: binding.skill_id,
          skillVersionId: binding.skill_version_id,
        })
      ) ?? [],
    retries: preset.retries ?? DEFAULT_RETRIES,
    enableThinking: preset.enable_thinking ?? true,
    enableInternetAccess: preset.enable_internet_access ?? false,
  }
}

function formValuesToPayload(values: AgentPresetFormValues): AgentPresetCreate {
  const outputType =
    values.outputTypeKind === "none"
      ? null
      : values.outputTypeKind === "data-type"
        ? (values.outputTypeDataType ?? null)
        : values.outputTypeJson
          ? JSON.parse(values.outputTypeJson)
          : null

  return {
    name: values.name.trim(),
    slug: values.slug.trim(),
    description: normalizeOptional(values.description),
    instructions:
      values.instructions && values.instructions.trim().length > 0
        ? values.instructions
        : null,
    model_name: values.model_name.trim(),
    model_provider: values.model_provider.trim(),
    catalog_id: values.catalog_id ? values.catalog_id : null,
    base_url: normalizeOptional(values.base_url),
    output_type: outputType ?? null,
    actions: values.actions.length > 0 ? values.actions : null,
    namespaces: values.namespaces.length > 0 ? values.namespaces : null,
    mcp_integrations:
      values.mcpIntegrations.length > 0 ? values.mcpIntegrations : null,
    agents: formValuesToAgentsPayload(values),
    skills: values.skills.map((binding) => ({
      skill_id: binding.skillId,
      skill_version_id: binding.skillVersionId,
    })),
    tool_approvals: toToolApprovalMap(values.toolApprovals),
    retries: values.retries,
    enable_thinking: values.enableThinking,
    enable_internet_access: values.enableInternetAccess,
  }
}

function formValuesToAgentsPayload(
  values: AgentPresetFormValues
): AgentPresetCreate["agents"] {
  if (!values.agentsEnabled) {
    return { enabled: false }
  }

  const subagents = values.subagents
    .map((subagent): AttachedSubagentRef | null => {
      const preset = subagent.preset.trim()
      if (!preset) {
        return null
      }

      const payload: AttachedSubagentRef = { preset }
      const name = normalizeOptional(subagent.name)
      const description = normalizeOptional(subagent.description)
      const presetVersion = parseOptionalPositiveInteger(subagent.presetVersion)
      const maxTurns = parseOptionalPositiveInteger(subagent.maxTurns)

      if (name !== null) {
        payload.name = name
      }
      if (description !== null) {
        payload.description = description
      }
      if (presetVersion !== null) {
        payload.preset_version = presetVersion
      }
      if (maxTurns !== null) {
        payload.max_turns = maxTurns
      }

      return payload
    })
    .filter((subagent): subagent is AttachedSubagentRef => subagent !== null)

  return {
    enabled: true,
    subagents,
  }
}

function formatSkillVersionLabel(version: SkillVersionRead): string {
  const name = version.name.trim()
  return name ? `v${version.version} · ${name}` : `v${version.version}`
}

function normalizeOptional(value: string | null | undefined) {
  if (value == null) {
    return null
  }
  const trimmed = value.trim()
  return trimmed.length > 0 ? trimmed : null
}

function parseOptionalPositiveInteger(value: string | null | undefined) {
  const trimmed = value?.trim()
  if (!trimmed) {
    return null
  }
  return Number.parseInt(trimmed, 10)
}

function toToolApprovalMap(
  approvals: ToolApprovalFormValue[]
): Record<string, boolean> | null {
  const entries = approvals
    .map(({ tool, allow }) => ({
      tool: tool.trim(),
      allow,
    }))
    .filter(({ tool }) => tool.length > 0)

  if (entries.length === 0) {
    return null
  }

  return Object.fromEntries(entries.map(({ tool, allow }) => [tool, allow]))
}
