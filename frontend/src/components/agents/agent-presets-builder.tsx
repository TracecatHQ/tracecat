"use client"

import { zodResolver } from "@hookform/resolvers/zod"
import {
  AlertCircle,
  Box,
  ChevronRight,
  Globe,
  Loader2,
  type LucideIcon,
  MessageCircle,
  Minus,
  MousePointerClickIcon,
  Plus,
  Pyramid,
  ShieldCheck,
  SlidersHorizontal,
  Sparkles,
  Webhook,
} from "lucide-react"
import Link from "next/link"
import { useRouter, useSearchParams } from "next/navigation"
import {
  type FormEvent,
  useCallback,
  useEffect,
  useId,
  useMemo,
  useRef,
  useState,
} from "react"
import {
  type FieldErrors,
  type UseFormReturn,
  useFieldArray,
  useForm,
  useWatch,
} from "react-hook-form"
import { z } from "zod"
import type {
  AgentPresetCapability,
  AgentPresetCreate,
  AgentPresetRead,
  AgentPresetReadMinimal,
  AgentPresetSubagentEligibility,
  AgentPresetUpdate,
  AnyAttachedSubagentRef,
  MCPIntegrationRead,
  RegistryActionReadMinimal,
  SkillReadMinimal,
} from "@/client"
import {
  AgentModelCombobox,
  buildEnabledModelOptions,
  type EnabledModelOption,
  findEnabledModelOption,
} from "@/components/agents/agent-model-combobox"
import { AgentPresetDetailActions } from "@/components/agents/agent-preset-detail-actions"
import { AgentPresetToolsList } from "@/components/agents/agent-preset-tools-list"
import { AgentPresetVersionSelect } from "@/components/agents/agent-preset-version-select"
import { SlackChannelPanel } from "@/components/agents/external-channels/slack-channel-panel"
import {
  ChatHistoryDropdown,
  type ChatHistoryScope,
} from "@/components/chat/chat-history-dropdown"
import { ChatSessionPane } from "@/components/chat/chat-session-pane"
import { CodeEditor } from "@/components/editor/codemirror/code-editor"
import { getModelProviderIconId, ProviderIcon } from "@/components/icons"
import { CenteredSpinner } from "@/components/loading/spinner"
import { SimpleEditor } from "@/components/tiptap-templates/simple/simple-editor"
import { Alert, AlertDescription, AlertTitle } from "@/components/ui/alert"
import { Badge } from "@/components/ui/badge"
import { Button } from "@/components/ui/button"
import {
  CommandDialog,
  CommandEmpty,
  CommandGroup,
  CommandInput,
  CommandItem,
  CommandList,
} from "@/components/ui/command"
import { DialogDescription } from "@/components/ui/dialog"
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
  FormField,
  FormItem,
  FormLabel,
  FormMessage,
} from "@/components/ui/form"
import {
  HoverCard,
  HoverCardContent,
  HoverCardTrigger,
} from "@/components/ui/hover-card"
import { Input } from "@/components/ui/input"
import {
  ResizableHandle,
  ResizablePanel,
  ResizablePanelGroup,
} from "@/components/ui/resizable"
import { ScrollArea } from "@/components/ui/scroll-area"
import { Separator } from "@/components/ui/separator"
import { Switch } from "@/components/ui/switch"
import { Tabs, TabsContent, TabsList, TabsTrigger } from "@/components/ui/tabs"
import { Textarea } from "@/components/ui/textarea"
import { ToggleTabs } from "@/components/ui/toggle-tabs"
import {
  Tooltip,
  TooltipContent,
  TooltipTrigger,
} from "@/components/ui/tooltip"
import {
  useAgentPreset,
  useAgentPresets,
  useAgentPresetToolPolicyPreview,
  useAgentPresetVersion,
  useAgentPresetVersions,
  useCreateAgentPreset,
  useUpdateAgentPreset,
} from "@/hooks/use-agent-presets"
import { useAuth } from "@/hooks/use-auth"
import {
  useCreateChat,
  useGetChatVercel,
  useListChats,
  useUpdateChat,
} from "@/hooks/use-chat"
import { useEntitlements } from "@/hooks/use-entitlements"
import { useSkills } from "@/hooks/use-skills"
import {
  type AgentOutputPrimitive,
  formatAgentOutputType,
  parseAgentOutputType,
} from "@/lib/agent-preset-output"
import {
  AGENT_PRESET_PUBLISHING_FIELDS,
  type AgentPresetFormMode,
  buildAgentPresetUpdatePayload,
  buildSkillCommandItemValue,
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
import {
  type AgentPresetDetailActionsState,
  useAgentPresetDetailContext,
} from "@/providers/agent-preset-detail"
import { useWorkspaceId } from "@/providers/workspace-id"

const DEFAULT_RETRIES = 3
const SUBAGENT_ALIAS_REGEX = /^[a-z0-9](?:[a-z0-9-]*[a-z0-9])?$/
const POSITIVE_INTEGER_REGEX = /^[1-9]\d*$/
const RESERVED_SUBAGENT_ALIASES = new Set([
  "agent",
  "general-purpose",
  "root",
  "task",
])
const AGENT_PRESET_TAB_QUERY_PARAM = "tab"

function AgentPresetLoadError({
  title,
  detail,
}: {
  title: string
  detail: string
}) {
  return (
    <div className="flex h-full items-center justify-center px-6">
      <Alert variant="destructive" className="max-w-xl">
        <AlertTitle>{title}</AlertTitle>
        <AlertDescription>{detail}</AlertDescription>
      </Alert>
    </div>
  )
}

/** Validation shared by the preset editor and its panel tests. */
export const agentPresetSchema = z
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
    subagents: z
      .array(
        z.object({
          preset: z.string().default(""),
          presetId: z.string().default(""),
          presetVersionId: z.string().default(""),
          name: z.string().default(""),
          description: z.string().max(1000).default(""),
          maxTurns: z.string().default(""),
        })
      )
      .default([]),
    skills: z
      .array(
        z.object({
          skillId: z.string().trim().min(1, "Select a skill"),
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
  })

/** Editable fields for the agent preset builder. */
export type AgentPresetFormValues = z.infer<typeof agentPresetSchema>
type SubagentFormValue = AgentPresetFormValues["subagents"][number]
type SkillBindingFormValue = AgentPresetFormValues["skills"][number]
type ToolApprovalFormValue = AgentPresetFormValues["toolApprovals"][number]

const LIVE_INTERNET_ACCESS_WARNING_MESSAGE =
  "One or more selected subagents have internet access enabled, but the parent agent does not. Enable internet access on the parent agent for those subagents to use web tools."

const AGENT_PRESET_CAPABILITY_CONFIG = [
  {
    capability: "approvals",
    label: "Approvals",
    Icon: ShieldCheck,
  },
  {
    capability: "subagents",
    label: "Subagents",
    Icon: MousePointerClickIcon,
  },
  {
    capability: "internet_access",
    label: "Internet access",
    Icon: Globe,
  },
] satisfies Array<{
  capability: AgentPresetCapability
  label: string
  Icon: LucideIcon
}>

const EMPTY_MCP_INTEGRATIONS: MCPIntegrationRead[] = []

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
  const searchParams = useSearchParams()
  const workspaceId = useWorkspaceId()
  const activePresetId = presetId
  const queryTab = parseAgentPresetSideTab(
    searchParams.get(AGENT_PRESET_TAB_QUERY_PARAM)
  )

  const { presets, presetsIsLoading, presetsError } =
    useAgentPresets(workspaceId)
  const { registryActions, registryActionsError } = useRegistryActions({
    staleTime: 5 * 60 * 1000,
  })
  const { models, providers } = useWorkspaceAgentModels(workspaceId)
  const enabledModelsLoaded = models !== undefined

  const { mcpIntegrations, mcpIntegrationsError } =
    useListMcpIntegrations(workspaceId)

  const { createAgentPreset, createAgentPresetIsPending } =
    useCreateAgentPreset(workspaceId)
  const { updateAgentPreset, updateAgentPresetIsPending } =
    useUpdateAgentPreset(workspaceId)

  const handleSetSelectedPresetId = useCallback(
    (nextId: string) => {
      if (!workspaceId) {
        return
      }
      const normalizedId = nextId?.trim() || activePresetId
      if (!normalizedId) {
        return
      }
      if (normalizedId === activePresetId) {
        return
      }
      const nextPath = `/workspaces/${workspaceId}/agents/${normalizedId}`
      const params = new URLSearchParams(searchParams.toString())
      const queryString = params.toString()
      router.replace(queryString ? `${nextPath}?${queryString}` : nextPath)
    },
    [activePresetId, router, searchParams, workspaceId]
  )

  const handleTabChange = useCallback(
    (tab: AgentPresetSideTab) => {
      const params = new URLSearchParams(searchParams.toString())
      params.set(AGENT_PRESET_TAB_QUERY_PARAM, tab)
      const queryString = params.toString()
      const path = activePresetId
        ? `/workspaces/${workspaceId}/agents/${activePresetId}`
        : `/workspaces/${workspaceId}/agents`
      router.replace(queryString ? `${path}?${queryString}` : path)
    },
    [activePresetId, router, searchParams, workspaceId]
  )

  const {
    preset: selectedPreset,
    presetIsLoading: selectedPresetIsLoading,
    presetError: selectedPresetError,
  } = useAgentPreset(workspaceId, activePresetId)

  const enabledModelOptions = useMemo(
    () => buildEnabledModelOptions(models, providers),
    [models, providers]
  )

  if (presetsIsLoading) {
    return <CenteredSpinner />
  }

  if (presetsError) {
    return (
      <AgentPresetLoadError
        title="Unable to load agent presets"
        detail={
          getApiErrorDetail(presetsError) ?? "Agent presets failed to load."
        }
      />
    )
  }

  if (activePresetId && selectedPresetIsLoading) {
    return <CenteredSpinner />
  }

  if (activePresetId && (selectedPresetError || !selectedPreset)) {
    return (
      <AgentPresetLoadError
        title="Unable to load agent preset"
        detail={
          getApiErrorDetail(selectedPresetError) ??
          "Agent preset was not found."
        }
      />
    )
  }

  return (
    <div className="flex h-full w-full flex-col overflow-hidden">
      <AgentPresetForm
        key={selectedPreset?.id ?? activePresetId}
        preset={selectedPreset ?? null}
        mode={selectedPreset ? "edit" : "create"}
        workspaceId={workspaceId}
        agentPresets={presets ?? []}
        registryActions={registryActions}
        registryLoading={registryActions === undefined}
        mcpLoading={mcpIntegrations === undefined}
        toolsLoadError={Boolean(registryActionsError || mcpIntegrationsError)}
        enabledModelOptions={enabledModelOptions}
        enabledModelsLoaded={enabledModelsLoaded}
        mcpIntegrations={mcpIntegrations ?? EMPTY_MCP_INTEGRATIONS}
        isSaving={
          selectedPreset
            ? updateAgentPresetIsPending
            : createAgentPresetIsPending
        }
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
        initialTab={queryTab ?? "live-chat"}
        onTabChange={handleTabChange}
      />
    </div>
  )
}

/**
 * Embeds the preset builder in surfaces that need a stacked, narrow layout.
 */
export function AgentPresetArtifactView({
  preset,
  workspaceId,
  initialTab,
  onTabChange,
}: {
  preset: AgentPresetRead
  workspaceId: string
  initialTab?: string | null
  onTabChange?: (tab: string) => void
}) {
  const { presets } = useAgentPresets(workspaceId)
  const { registryActions, registryActionsError } = useRegistryActions({
    staleTime: 5 * 60 * 1000,
  })
  const { models, providers } = useWorkspaceAgentModels(workspaceId)
  const enabledModelsLoaded = models !== undefined
  const { mcpIntegrations, mcpIntegrationsError } =
    useListMcpIntegrations(workspaceId)
  const { updateAgentPreset, updateAgentPresetIsPending } =
    useUpdateAgentPreset(workspaceId)

  const enabledModelOptions = useMemo(
    () => buildEnabledModelOptions(models, providers),
    [models, providers]
  )

  return (
    <AgentPresetForm
      key={preset.id}
      preset={preset}
      mode="edit"
      workspaceId={workspaceId}
      agentPresets={presets ?? []}
      onCreate={async () => {
        throw new Error("Agent artifacts cannot create presets.")
      }}
      onUpdate={async (presetId, payload) => {
        return await updateAgentPreset({
          presetId,
          ...payload,
        })
      }}
      isSaving={updateAgentPresetIsPending}
      registryActions={registryActions}
      registryLoading={registryActions === undefined}
      mcpLoading={mcpIntegrations === undefined}
      toolsLoadError={Boolean(registryActionsError || mcpIntegrationsError)}
      enabledModelOptions={enabledModelOptions}
      enabledModelsLoaded={enabledModelsLoaded}
      mcpIntegrations={mcpIntegrations ?? EMPTY_MCP_INTEGRATIONS}
      layout="stacked"
      initialTab={parseAgentPresetSideTab(initialTab) ?? "configuration"}
      onTabChange={onTabChange}
    />
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
  const { user } = useAuth()
  const [selectedChatId, setSelectedChatId] = useState<string | null>(null)
  const [historyScope, setHistoryScope] = useState<ChatHistoryScope>("team")

  const { chats, chatsLoading, chatsError, refetchChats } = useListChats(
    {
      workspaceId,
      entityType: "agent_preset",
      entityId: preset?.id,
      createdBy: historyScope === "mine" ? user?.id : undefined,
    },
    { enabled: Boolean(preset && workspaceId) }
  )

  useEffect(() => {
    setSelectedChatId(null)
  }, [preset?.id])

  const latestChatId =
    chats?.find((candidate) => !candidate.is_readonly)?.id ?? chats?.[0]?.id
  const activeChatId = selectedChatId ?? latestChatId

  const handleHistoryScopeChange = (nextScope: ChatHistoryScope) => {
    setHistoryScope(nextScope)
    setSelectedChatId(null)
  }

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
  const {
    presetVersion: selectedVersionConfig,
    presetVersionIsLoading: selectedVersionConfigIsLoading,
  } = useAgentPresetVersion(workspaceId, preset?.id, selectedVersionId, {
    enabled: Boolean(workspaceId && preset?.id && selectedVersionId),
  })
  const effectiveModelConfig = selectedVersionId
    ? (selectedVersionConfig ?? null)
    : preset
  const selectedModel = useMemo(
    () =>
      effectiveModelConfig
        ? findEnabledModelOption(enabledModelOptions, {
            catalogId: effectiveModelConfig.catalog_id,
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
      iconId: selectedModel?.iconId ?? getModelProviderIconId(provider),
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
        agent_preset_version_id: selectedVersionId,
      })
      setSelectedChatId(newChat.id)
      await refetchChats()
    } catch (error) {
      console.error("Failed to create agent preset chat", error)
    }
  }

  const handlePresetVersionChange = async (nextVersionId: string | null) => {
    if (!activeChatId || chat?.is_readonly) {
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

    if (
      enabledModelsLoaded &&
      !selectedModel &&
      !hasLegacyModelConfig &&
      !chat?.is_readonly
    ) {
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

    if (
      !activeChatId ||
      chatLoading ||
      chatsLoading ||
      !chat ||
      (!modelInfo && !chat.is_readonly)
    ) {
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
        modelInfo={modelInfo ?? undefined}
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
            disabled={!activeChatId || !chat || chat.is_readonly || isUpdating}
            triggerClassName="h-8 w-[10.5rem] text-xs"
          />
          <div className="flex items-center gap-1">
            <ChatHistoryDropdown
              chats={chats}
              isLoading={chatsLoading}
              error={chatsError}
              selectedChatId={activeChatId ?? undefined}
              onSelectChat={(chatId) => setSelectedChatId(chatId)}
              workspaceId={workspaceId}
              scope={historyScope}
              onScopeChange={handleHistoryScopeChange}
              align="end"
            />
            <Button
              type="button"
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

const AGENT_PRESET_SIDE_TABS = new Set<AgentPresetSideTab>([
  "live-chat",
  "assistant",
  "configuration",
  "subagents",
  "skills",
  "channels",
  "structured-output",
])

function parseAgentPresetSideTab(
  value: string | null | undefined
): AgentPresetSideTab | null {
  if (!value || !AGENT_PRESET_SIDE_TABS.has(value as AgentPresetSideTab)) {
    return null
  }
  return value as AgentPresetSideTab
}

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

  if (errors.subagents) {
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

/**
 * Maps every form field onto the backend preset field it writes through
 * `formValuesToPayload`. `null` marks fields that only exist in the form.
 * Used to decide whether an edit will cut a new version.
 */
const AGENT_PRESET_FORM_FIELD_TO_BACKEND_FIELD: Record<
  keyof AgentPresetFormValues,
  string | null
> = {
  name: "name",
  slug: "slug",
  description: "description",
  instructions: "instructions",
  source_id: null,
  catalog_id: "catalog_id",
  model_provider: "model_provider",
  model_name: "model_name",
  base_url: "base_url",
  outputTypeKind: "output_type",
  outputTypeDataType: "output_type",
  outputTypeJson: "output_type",
  actions: "actions",
  namespaces: "namespaces",
  mcpIntegrations: "mcp_integrations",
  subagents: "agents",
  skills: "skills",
  toolApprovals: "tool_approvals",
  retries: "retries",
  enableThinking: "enable_thinking",
  enableInternetAccess: "enable_internet_access",
}

/**
 * Skill bindings are versioned outside `EXECUTION_FIELDS` but still cut a new
 * version, so they count as publishing changes for labelling purposes.
 */
const AGENT_PRESET_PUBLISHING_BACKEND_FIELDS: ReadonlySet<string> = new Set([
  ...AGENT_PRESET_PUBLISHING_FIELDS,
  "skills",
])

function isDirtyFormValue(value: unknown): boolean {
  if (Array.isArray(value)) {
    return value.some(isDirtyFormValue)
  }
  if (value !== null && typeof value === "object") {
    return Object.values(value).some(isDirtyFormValue)
  }
  return value === true
}

/**
 * Labels the primary submit button. The backend only cuts a new version when a
 * publishing field changes, so a metadata-only edit is just a save.
 */
function getAgentPresetSubmitLabel({
  mode,
  dirtyFields,
}: {
  mode: AgentPresetFormMode
  dirtyFields: Partial<Record<keyof AgentPresetFormValues, unknown>>
}): string {
  if (mode === "create") {
    return "Publish version"
  }

  for (const [field, value] of Object.entries(dirtyFields)) {
    if (!isDirtyFormValue(value)) {
      continue
    }
    const backendField =
      AGENT_PRESET_FORM_FIELD_TO_BACKEND_FIELD[
        field as keyof AgentPresetFormValues
      ]
    if (
      backendField &&
      AGENT_PRESET_PUBLISHING_BACKEND_FIELDS.has(backendField)
    ) {
      return "Publish version"
    }
  }

  return "Save changes"
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

function hasSelectedStdioMcpIntegration(
  selectedIds: string[] | undefined,
  integrations: MCPIntegrationRead[]
): boolean {
  if (!selectedIds?.length) {
    return false
  }

  const selected = new Set(selectedIds)
  return integrations.some(
    (integration) =>
      integration.server_type === "stdio" && selected.has(integration.id)
  )
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
  isSaving: boolean
  registryActions?: RegistryActionReadMinimal[]
  registryLoading: boolean
  mcpLoading: boolean
  toolsLoadError: boolean
  enabledModelOptions: EnabledModelOption[]
  enabledModelsLoaded: boolean
  mcpIntegrations: MCPIntegrationRead[]
  layout?: "split" | "stacked"
  initialTab?: AgentPresetSideTab
  onTabChange?: (tab: AgentPresetSideTab) => void
}

function AgentPresetForm({
  preset,
  mode,
  workspaceId,
  agentPresets,
  builderPrompt,
  onCreate,
  onUpdate,
  isSaving,
  registryActions,
  registryLoading,
  mcpLoading,
  toolsLoadError,
  enabledModelOptions,
  enabledModelsLoaded,
  mcpIntegrations,
  layout = "split",
  initialTab = "live-chat",
  onTabChange,
}: AgentPresetFormProps) {
  const [activeTab, setActiveTab] = useState<AgentPresetSideTab>(initialTab)
  const { hasEntitlement } = useEntitlements()
  const channelsEnabled = hasEntitlement("agent_channels")
  const form = useForm<AgentPresetFormValues>({
    resolver: zodResolver(agentPresetSchema),
    mode: "onBlur",
    defaultValues: preset ? presetToFormValues(preset) : DEFAULT_FORM_VALUES,
  })
  const watchedMcpIntegrations =
    useWatch({ control: form.control, name: "mcpIntegrations" }) ?? []
  const hasStdioMcp = useMemo(
    () =>
      hasSelectedStdioMcpIntegration(watchedMcpIntegrations, mcpIntegrations),
    [mcpIntegrations, watchedMcpIntegrations]
  )
  const agentPresetsBySlug = useMemo(
    () => new Map(agentPresets.map((preset) => [preset.slug, preset])),
    [agentPresets]
  )
  const agentPresetsById = useMemo(
    () => new Map(agentPresets.map((preset) => [preset.id, preset])),
    [agentPresets]
  )
  useEffect(() => {
    setActiveTab(initialTab)
  }, [initialTab])

  const handleTabChange = useCallback(
    (tab: AgentPresetSideTab) => {
      setActiveTab(tab)
      onTabChange?.(tab)
    },
    [onTabChange]
  )

  const {
    fields: skillFields,
    append: appendSkillBinding,
    remove: removeSkillBinding,
  } = useFieldArray({
    control: form.control,
    name: "skills",
  })

  const {
    fields: subagentFields,
    append: appendSubagent,
    remove: removeSubagent,
  } = useFieldArray({
    control: form.control,
    name: "subagents",
  })

  const previousFormResetKey = useRef<string | null>(null)
  useEffect(() => {
    const defaults = preset ? presetToFormValues(preset) : DEFAULT_FORM_VALUES
    const resetKey = JSON.stringify({ mode, presetId: preset?.id, defaults })
    // Metadata toggles outside the form must not discard unpublished edits.
    if (previousFormResetKey.current === resetKey) {
      return
    }
    previousFormResetKey.current = resetKey
    form.reset(defaults, { keepDirty: false })
  }, [form, mode, preset])

  useEffect(() => {
    if (hasStdioMcp && !form.getValues("enableInternetAccess")) {
      form.setValue("enableInternetAccess", true, { shouldDirty: true })
    }
  }, [form, hasStdioMcp])

  const watchedName = form.watch("name")
  const catalogId = form.watch("catalog_id")
  const sourceId = form.watch("source_id")
  const modelProvider = form.watch("model_provider")
  const modelName = form.watch("model_name")
  const baseUrl = form.watch("base_url")
  const selectedModel = useMemo(
    () =>
      findEnabledModelOption(enabledModelOptions, {
        catalogId,
        sourceId,
        modelProvider,
        modelName,
        baseUrl,
      }),
    [
      baseUrl,
      catalogId,
      enabledModelOptions,
      sourceId,
      modelName,
      modelProvider,
    ]
  )

  useEffect(() => {
    if (mode === "edit") {
      return
    }
    const nextSlug = slugify(watchedName ?? "", "-")
    if (form.getValues("slug") !== nextSlug) {
      form.setValue("slug", nextSlug, { shouldDirty: false })
    }
  }, [form, mode, watchedName])

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
      const eligibilityIssue = getFirstSubagentEligibilityIssue({
        subagents: values.subagents,
        presetsById: agentPresetsById,
        presetsBySlug: agentPresetsBySlug,
      })
      if (eligibilityIssue) {
        form.setError(
          `subagents.${eligibilityIssue.index}.${eligibilityIssue.field}`,
          {
            type: "manual",
            message: eligibilityIssue.message,
          }
        )
        handleTabChange("subagents")
        return
      }

      const payload = formValuesToPayload(values, {
        forceInternetAccess:
          hasStdioMcp ||
          hasSelectedStdioMcpIntegration(
            values.mcpIntegrations,
            mcpIntegrations
          ),
      })
      if (mode === "edit" && preset) {
        const updatePayload = buildAgentPresetUpdatePayload(payload, {
          skillsChanged: Boolean(form.formState.dirtyFields.skills),
        })
        const updated = await onUpdate(preset.id, updatePayload)
        form.reset(presetToFormValues(updated))
      } else {
        const created = await onCreate(payload)
        form.reset(presetToFormValues(created))
      }
    },
    (errors) => {
      const nextTab = getAgentPresetErrorTab(errors)
      if (nextTab) {
        handleTabChange(nextTab)
      }
    }
  )

  const canSubmit =
    form.formState.isDirty ||
    (mode === "create" &&
      Boolean(form.watch("name")) &&
      Boolean(form.watch("model_provider")) &&
      Boolean(form.watch("model_name")))

  const getDraftPayload = useCallback((): AgentPresetCreate | null => {
    try {
      return formValuesToPayload(form.getValues(), {
        forceInternetAccess: hasStdioMcp,
      })
    } catch {
      // `formValuesToPayload` runs `JSON.parse` on the structured-output
      // schema, which legitimately throws while the user is mid-edit.
      return null
    }
  }, [form, hasStdioMcp])

  // Wording only: the backend cuts a version only when a publishing field
  // changes, so a metadata-only edit reads as "Save changes". RHF marks array
  // fields such as `subagents` dirty even after an edit-then-undo, which can
  // over-report publishing here. It never gates the mutation.
  const submitLabel = getAgentPresetSubmitLabel({
    mode,
    dirtyFields: form.formState.dirtyFields,
  })

  // Everything registered below must have a stable identity, because the
  // registration effect writes to provider state and this component consumes
  // that provider: an unstable dependency re-fires the effect on every render
  // and loops forever. `form.handleSubmit(...)` returns a new function each
  // render, so route the callbacks through refs so the registered object only
  // changes when the values the header actually renders change.
  const handleSubmitRef = useRef(handleSubmit)
  const getDraftPayloadRef = useRef(getDraftPayload)
  useEffect(() => {
    handleSubmitRef.current = handleSubmit
    getDraftPayloadRef.current = getDraftPayload
  })
  const submit = useCallback(() => {
    void handleSubmitRef.current()
  }, [])
  const getDraftPayloadStable = useCallback(
    (): AgentPresetCreate | null => getDraftPayloadRef.current(),
    []
  )

  const detail = useAgentPresetDetailContext()
  const registerDetailActions = detail?.registerActions
  const presetIdValue = preset?.id ?? null
  const presetCurrentVersionId = preset?.current_version_id ?? null
  const detailActions = useMemo<AgentPresetDetailActionsState>(
    () => ({
      workspaceId,
      presetId: presetIdValue,
      currentVersionId: presetCurrentVersionId,
      getDraftPayload: getDraftPayloadStable,
      isSaving,
      canSubmit,
      submitLabel,
      submit,
    }),
    [
      canSubmit,
      getDraftPayloadStable,
      isSaving,
      presetCurrentVersionId,
      presetIdValue,
      submit,
      submitLabel,
      workspaceId,
    ]
  )

  // When `AgentPresetDetailProvider` is mounted (the standalone preset
  // route), register the actions so the global controls header renders
  // them; without a provider (case artifact view) the document panel
  // renders them inline instead.
  useEffect(() => {
    if (!registerDetailActions) {
      return
    }
    registerDetailActions(detailActions)
    return () => registerDetailActions(null)
  }, [detailActions, registerDetailActions])

  function handleAddSubagent(subagent: SubagentFormValue) {
    appendSubagent(subagent, { shouldFocus: false })
  }

  const handleAddSkillBinding = useCallback(
    (binding: SkillBindingFormValue) => {
      appendSkillBinding(binding)
    },
    [appendSkillBinding]
  )

  const documentPanel = (
    <AgentPresetDocumentPanel
      form={form}
      workspaceId={workspaceId}
      presetId={preset?.id ?? null}
      currentVersionId={preset?.current_version_id ?? null}
      getDraftPayload={getDraftPayload}
      isSaving={isSaving}
      canSubmit={canSubmit}
      submitLabel={submitLabel}
      onPublish={submit}
    />
  )

  const rightPanel = (
    <AgentPresetRightPanel
      activeTab={effectiveTab}
      onTabChange={handleTabChange}
      channelsEnabled={channelsEnabled}
      preset={preset}
      workspaceId={workspaceId}
      agentPresets={agentPresets}
      builderPrompt={builderPrompt}
      form={form}
      isSaving={isSaving}
      registryActions={registryActions}
      registryLoading={registryLoading}
      mcpLoading={mcpLoading}
      toolsLoadError={toolsLoadError}
      enabledModelOptions={enabledModelOptions}
      enabledModelsLoaded={enabledModelsLoaded}
      mcpIntegrations={mcpIntegrations}
      hasStdioMcp={hasStdioMcp}
      skillFields={skillFields}
      onAddSkillBinding={handleAddSkillBinding}
      onRemoveSkillBinding={removeSkillBinding}
      subagentFields={subagentFields}
      onAddSubagent={handleAddSubagent}
      onRemoveSubagent={removeSubagent}
    />
  )

  const handleFormSubmit = (event: FormEvent<HTMLFormElement>) => {
    event.preventDefault()
    // Ignore submits bubbling from nested forms (e.g., chat prompt inputs).
    if (event.target !== event.currentTarget) {
      return
    }
    void handleSubmit()
  }

  return (
    <Form {...form}>
      <form onSubmit={handleFormSubmit} className="h-full min-h-0">
        {layout === "stacked" ? (
          <ResizablePanelGroup direction="vertical" className="h-full">
            <ResizablePanel
              defaultSize={42}
              minSize={28}
              className="overflow-hidden"
            >
              {documentPanel}
            </ResizablePanel>

            <ResizableHandle />

            <ResizablePanel
              defaultSize={58}
              minSize={32}
              className="overflow-hidden"
            >
              {rightPanel}
            </ResizablePanel>
          </ResizablePanelGroup>
        ) : (
          <ResizablePanelGroup direction="horizontal" className="h-full">
            <ResizablePanel defaultSize={62} minSize={40}>
              {documentPanel}
            </ResizablePanel>

            <ResizableHandle withHandle />

            <ResizablePanel defaultSize={38} minSize={26}>
              {rightPanel}
            </ResizablePanel>
          </ResizablePanelGroup>
        )}
      </form>
    </Form>
  )
}

function AgentPresetDocumentPanel({
  form,
  workspaceId,
  presetId,
  currentVersionId,
  getDraftPayload,
  isSaving,
  canSubmit,
  submitLabel,
  onPublish,
}: {
  form: UseFormReturn<AgentPresetFormValues>
  workspaceId: string
  presetId: string | null
  currentVersionId: string | null
  getDraftPayload: () => AgentPresetCreate | null
  isSaving: boolean
  canSubmit: boolean
  submitLabel: string
  onPublish: () => void
}) {
  // Context present -> the standalone route's global controls header renders
  // the detail actions; context absent (case artifact view) -> render them
  // inline next to the title.
  const detail = useAgentPresetDetailContext()
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
          {detail ? null : (
            <div className="flex items-center gap-2">
              <AgentPresetDetailActions
                workspaceId={workspaceId}
                presetId={presetId}
                currentVersionId={currentVersionId}
                getDraftPayload={getDraftPayload}
                isSaving={isSaving}
                canSubmit={canSubmit}
                submitLabel={submitLabel}
                onPublish={onPublish}
              />
            </div>
          )}
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
  registryActions,
  registryLoading,
  mcpLoading,
  toolsLoadError,
  enabledModelOptions,
  enabledModelsLoaded,
  mcpIntegrations,
  hasStdioMcp,
  skillFields,
  onAddSkillBinding,
  onRemoveSkillBinding,
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
  registryActions?: RegistryActionReadMinimal[]
  registryLoading: boolean
  mcpLoading: boolean
  toolsLoadError: boolean
  enabledModelOptions: EnabledModelOption[]
  enabledModelsLoaded: boolean
  mcpIntegrations: MCPIntegrationRead[]
  hasStdioMcp: boolean
  skillFields: Array<{ id: string }>
  onAddSkillBinding: (binding: SkillBindingFormValue) => void
  onRemoveSkillBinding: (index: number) => void
  subagentFields: Array<{ id: string }>
  onAddSubagent: (subagent: SubagentFormValue) => void
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
                <MousePointerClickIcon className="mr-2 size-4" />
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
              workspaceId={workspaceId}
              savedSkillIds={
                preset
                  ? (preset.skills ?? []).map((skill) => skill.skill_id)
                  : undefined
              }
              maxTools={preset?.tool_policy?.max_tools}
              savedSkillActions={preset?.tool_policy?.skill_actions}
              savedNamespaces={preset?.namespaces}
              form={form}
              isSaving={isSaving}
              registryActions={registryActions}
              registryLoading={registryLoading}
              mcpLoading={mcpLoading}
              toolsLoadError={toolsLoadError}
              enabledModelOptions={enabledModelOptions}
              enabledModelsLoaded={enabledModelsLoaded}
              mcpIntegrations={mcpIntegrations}
              hasStdioMcp={hasStdioMcp}
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
              savedBindings={preset?.skills}
              onAddSkillBinding={onAddSkillBinding}
              onRemoveSkillBinding={onRemoveSkillBinding}
            />
          </TabsContent>

          <TabsContent value="structured-output" className="mt-0 h-full">
            <AgentPresetStructuredOutputPanel form={form} isSaving={isSaving} />
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

/** Configure the model and tools, previewing skills from the current form. */
export function AgentPresetConfigurationPanel({
  workspaceId,
  savedSkillIds,
  savedNamespaces,
  form,
  isSaving,
  registryActions,
  registryLoading,
  mcpLoading,
  toolsLoadError,
  enabledModelOptions,
  enabledModelsLoaded,
  mcpIntegrations,
  hasStdioMcp,
  maxTools,
  savedSkillActions,
}: {
  workspaceId: string
  savedSkillIds?: string[]
  savedNamespaces?: string[] | null
  form: UseFormReturn<AgentPresetFormValues>
  isSaving: boolean
  registryActions?: RegistryActionReadMinimal[]
  registryLoading: boolean
  mcpLoading: boolean
  toolsLoadError: boolean
  enabledModelOptions: EnabledModelOption[]
  enabledModelsLoaded: boolean
  mcpIntegrations: MCPIntegrationRead[]
  hasStdioMcp: boolean
  maxTools?: number | null
  savedSkillActions?: string[] | null
}) {
  const skills = useWatch({ control: form.control, name: "skills" })
  const namespaces = useWatch({ control: form.control, name: "namespaces" })
  const selectedMcpIntegrations = useWatch({
    control: form.control,
    name: "mcpIntegrations",
  })
  const skillIds = [
    ...new Set(skills.map((skill) => skill.skillId).filter(Boolean)),
  ].sort()
  const savedIds = [...new Set(savedSkillIds?.filter(Boolean))].sort()
  const skillsChanged =
    savedSkillIds === undefined ||
    skillIds.length !== savedIds.length ||
    skillIds.some((id, index) => id !== savedIds[index])
  const namespaceFilters = [...new Set(namespaces)].sort()
  const savedFilters = [...new Set(savedNamespaces)].sort()
  const namespacesChanged =
    namespaceFilters.length !== savedFilters.length ||
    namespaceFilters.some(
      (namespace, index) => namespace !== savedFilters[index]
    )
  const policyChanged = skillsChanged || namespacesChanged
  const previewEnabled =
    savedSkillIds === undefined || (policyChanged && skillIds.length > 0)
  const { data: preview, isError: previewFailed } =
    useAgentPresetToolPolicyPreview(
      workspaceId,
      {
        actions: [],
        namespaces,
        mcp_integrations: selectedMcpIntegrations,
        skill_ids: skillIds,
        tool_approvals: {},
      },
      { enabled: previewEnabled }
    )
  let skillActions = savedSkillActions
  if (policyChanged) {
    skillActions =
      previewEnabled && !previewFailed ? preview?.skill_actions : undefined
  }
  const catalogId = form.watch("catalog_id")
  const sourceId = form.watch("source_id")
  const modelProvider = form.watch("model_provider")
  const modelName = form.watch("model_name")
  const baseUrl = form.watch("base_url")
  const thinkingEnabled = form.watch("enableThinking")
  const internetAccessEnabled = form.watch("enableInternetAccess")
  return (
    <ScrollArea className="h-full [&_[data-radix-scroll-area-viewport]>div]:!block [&_[data-radix-scroll-area-viewport]>div]:!w-full [&_[data-radix-scroll-area-viewport]>div]:!min-w-0 [&_[data-radix-scroll-area-viewport]>div]:!max-w-full">
      <div className="flex min-w-0 w-full flex-col gap-4 px-4 pt-4 pb-20 text-xs [--tool-list-inset:1rem]">
        <section className="min-w-0 w-full space-y-4">
          <div className="grid min-w-0 grid-cols-1 gap-4">
            <FormField
              control={form.control}
              name="model_name"
              render={({ field }) => (
                <FormItem className="flex min-w-0 items-center gap-3 space-y-0">
                  <FormLabel
                    className="w-32 shrink-0 text-xs font-normal text-muted-foreground"
                    // A label click also clicks its control, which would open
                    // the model list. Focus the trigger only.
                    onClick={(event) => {
                      event.preventDefault()
                      document
                        .getElementById(event.currentTarget.htmlFor)
                        ?.focus()
                    }}
                  >
                    Model
                  </FormLabel>
                  <FormControl>
                    <AgentModelCombobox
                      options={enabledModelOptions}
                      value={{
                        catalogId,
                        sourceId,
                        modelProvider,
                        modelName,
                        baseUrl,
                      }}
                      onChange={(option) => {
                        field.onChange(option.modelName)
                        syncFormModelSelection(form, option, true)
                      }}
                      loaded={enabledModelsLoaded}
                      disabled={isSaving || enabledModelOptions.length === 0}
                      placeholder={
                        enabledModelOptions.length
                          ? "Select a model"
                          : "No enabled models"
                      }
                    />
                  </FormControl>
                </FormItem>
              )}
            />
          </div>
          <div className="flex items-center gap-3">
            <label
              htmlFor="enable-thinking"
              className="w-32 shrink-0 text-muted-foreground"
            >
              Thinking
            </label>
            <Switch
              id="enable-thinking"
              checked={thinkingEnabled}
              onCheckedChange={(checked) =>
                form.setValue("enableThinking", checked, { shouldDirty: true })
              }
              disabled={isSaving}
            />
          </div>
          <div className="flex items-center gap-3">
            <label
              htmlFor="enable-internet-access"
              className="w-32 shrink-0 text-muted-foreground"
            >
              Internet access
            </label>
            {hasStdioMcp ? (
              <Tooltip>
                <TooltipTrigger asChild>
                  <span
                    className="inline-flex focus-visible:outline-none focus-visible:ring-1 focus-visible:ring-inset focus-visible:ring-ring"
                    tabIndex={0}
                  >
                    <Switch id="enable-internet-access" checked disabled />
                  </span>
                </TooltipTrigger>
                <TooltipContent>
                  Internet access is required when a stdio MCP server is
                  connected
                </TooltipContent>
              </Tooltip>
            ) : (
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
            )}
          </div>
          <FormField
            control={form.control}
            name="retries"
            render={({ field }) => (
              <FormItem className="flex items-start gap-3 space-y-0">
                <FormLabel className="w-32 shrink-0 pt-2 text-xs font-normal text-muted-foreground">
                  Retries
                </FormLabel>
                <div>
                  <FormControl>
                    <Input
                      type="number"
                      min={0}
                      {...field}
                      disabled={isSaving}
                      className="h-8 w-20 shadow-none"
                    />
                  </FormControl>
                  <FormMessage />
                </div>
              </FormItem>
            )}
          />
        </section>
        <Separator className="-mx-4 w-auto" />
        <AgentPresetToolsList
          registryActions={registryActions}
          registryLoading={registryLoading}
          mcpLoading={mcpLoading}
          toolsLoadError={toolsLoadError}
          mcpIntegrations={mcpIntegrations}
          isSaving={isSaving}
          maxTools={
            maxTools ??
            (savedSkillIds === undefined ? preview?.max_tools : undefined)
          }
          skillActions={skillActions}
        />
      </div>
    </ScrollArea>
  )
}

/** Edit attached subagents through a picker and flat, expandable property rows. */
export function AgentPresetSubagentsPanel({
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
  onAddSubagent: (subagent: SubagentFormValue) => void
  onRemoveSubagent: (index: number) => void
}) {
  const [isPickerOpen, setIsPickerOpen] = useState(false)
  const appendedRowIndex = useRef<number | null>(null)
  useEffect(() => {
    appendedRowIndex.current = null
  }, [subagentFields.length])
  const parentInternetAccessEnabled =
    useWatch({ control: form.control, name: "enableInternetAccess" }) ?? false
  const selectedSubagents =
    useWatch({ control: form.control, name: "subagents" }) ?? []
  const presetOptions = useMemo(
    () =>
      agentPresets
        .filter((preset) => preset.id !== parentPreset?.id)
        .sort((a, b) => a.name.localeCompare(b.name)),
    [agentPresets, parentPreset?.id]
  )
  const presetOptionsBySlug = useMemo(
    () => new Map(presetOptions.map((preset) => [preset.slug, preset])),
    [presetOptions]
  )
  const presetOptionsById = useMemo(
    () => new Map(presetOptions.map((preset) => [preset.id, preset])),
    [presetOptions]
  )
  const selectedInternetAccessSubagentAliases =
    getSelectedInternetAccessSubagentAliases({
      subagents: selectedSubagents,
      presetsById: presetOptionsById,
      presetsBySlug: presetOptionsBySlug,
    })
  const internetAccessWarningMessage = getInternetAccessWarningMessage({
    parentInternetAccessEnabled,
    selectedInternetAccessSubagentAliases,
  })
  const addPresetDisabledReason =
    presetOptions.length === 0
      ? "Create another agent preset first, then attach it here."
      : null
  const addPresetButton = (
    <Button
      type="button"
      size="sm"
      variant="outline"
      className="h-7 gap-1.5 text-xs shadow-none focus-visible:ring-1 focus-visible:ring-inset focus-visible:ring-ring"
      onClick={() => setIsPickerOpen(true)}
      disabled={isSaving || presetOptions.length === 0}
    >
      <Plus className="size-3.5" />
      Add subagent
    </Button>
  )

  function handleAddSubagent(preset: AgentPresetReadMinimal) {
    appendedRowIndex.current = subagentFields.length
    onAddSubagent({
      preset: preset.slug,
      presetId: preset.id,
      presetVersionId: preset.current_version_id ?? "",
      name: "",
      description: "",
      maxTurns: "",
    })
    setIsPickerOpen(false)
  }

  return (
    <div className="h-full overflow-auto pb-20 text-xs">
      <div className="flex h-14 items-center gap-2 border-b border-border/50 px-4">
        <h3 className="font-medium">Subagents</h3>
        <span className="text-muted-foreground">{subagentFields.length}</span>
        <div className="ml-auto">
          {addPresetDisabledReason ? (
            <Tooltip>
              <TooltipTrigger asChild>
                <span
                  className="inline-flex focus-visible:outline-none focus-visible:ring-1 focus-visible:ring-inset focus-visible:ring-ring"
                  tabIndex={0}
                >
                  {addPresetButton}
                </span>
              </TooltipTrigger>
              <TooltipContent>{addPresetDisabledReason}</TooltipContent>
            </Tooltip>
          ) : (
            addPresetButton
          )}
        </div>
      </div>
      {internetAccessWarningMessage ? (
        <Alert variant="warning" className="m-4 w-auto">
          <AlertCircle className="size-4" />
          <AlertTitle>Internet access limited</AlertTitle>
          <AlertDescription>{internetAccessWarningMessage}</AlertDescription>
        </Alert>
      ) : null}
      {subagentFields.length === 0 ? (
        <p className="px-4 py-3 text-muted-foreground">
          {presetOptions.length === 0
            ? "Create another agent preset first, then attach it here. Dynamic general-purpose subagents are already available."
            : "No preset subagents attached. General-purpose subagents can still run and inherit this agent's current scopes."}
        </p>
      ) : null}
      {subagentFields.map((item, index) => {
        const subagent = selectedSubagents[index]
        const preset = subagent
          ? getSubagentPreset({
              subagent,
              presetsById: presetOptionsById,
              presetsBySlug: presetOptionsBySlug,
            })
          : null
        return (
          <AgentPresetSubagentRow
            key={item.id}
            form={form}
            index={index}
            preset={preset}
            isSaving={isSaving}
            initiallyExpanded={appendedRowIndex.current === index}
            onRemove={onRemoveSubagent}
          />
        )
      })}
      <CommandDialog open={isPickerOpen} onOpenChange={setIsPickerOpen}>
        <DialogDescription className="sr-only">
          Choose a preset to attach as a subagent.
        </DialogDescription>
        <CommandInput placeholder="Search subagents..." />
        <CommandList>
          <CommandEmpty>No presets found.</CommandEmpty>
          <CommandGroup heading="Workspace presets">
            {presetOptions.map((preset) => {
              const eligibilityMessage = getSubagentEligibilityMessage(
                preset.current_version_subagent_eligibility
              )
              const capabilities = getOrderedAgentPresetCapabilities(preset)
              return (
                <HoverCard key={preset.id} openDelay={300}>
                  <HoverCardTrigger asChild>
                    <CommandItem
                      value={preset.id}
                      keywords={[preset.name, preset.slug]}
                      disabled={isSaving}
                      aria-description={eligibilityMessage ?? undefined}
                      onSelect={() => handleAddSubagent(preset)}
                      className="flex min-w-0 items-center gap-2"
                    >
                      <MousePointerClickIcon className="!size-4 shrink-0 text-primary" />
                      <span className="min-w-0 truncate">{preset.name}</span>
                      <span className="truncate font-mono text-xs text-muted-foreground">
                        {preset.slug}
                      </span>
                      <span className="ml-auto flex shrink-0 items-center gap-2">
                        <AgentPresetCapabilityIcons
                          capabilities={capabilities}
                        />
                        {eligibilityMessage ? (
                          <AlertCircle
                            className="!size-3.5 shrink-0 text-destructive"
                            aria-label="Cannot attach this preset"
                          />
                        ) : null}
                      </span>
                    </CommandItem>
                  </HoverCardTrigger>
                  {eligibilityMessage ? (
                    <HoverCardContent className="w-64 p-3 text-xs shadow-none">
                      {eligibilityMessage}
                    </HoverCardContent>
                  ) : null}
                </HoverCard>
              )
            })}
          </CommandGroup>
        </CommandList>
      </CommandDialog>
    </div>
  )
}

function AgentPresetSubagentRow({
  form,
  index,
  preset,
  isSaving,
  initiallyExpanded,
  onRemove,
}: {
  form: UseFormReturn<AgentPresetFormValues>
  index: number
  preset: AgentPresetReadMinimal | null
  isSaving: boolean
  initiallyExpanded: boolean
  onRemove: (index: number) => void
}) {
  const [isExpanded, setIsExpanded] = useState(initiallyExpanded)
  const subagent = useWatch({
    control: form.control,
    name: `subagents.${index}`,
  })
  const bodyId = useId()
  if (!subagent) return null
  const eligibilityIssue = preset
    ? getSubagentEligibilityIssue({ preset })
    : null
  const rowErrors = form.formState.errors.subagents?.[index]
  const presetError = rowErrors?.preset?.message
  const presetAliasError =
    presetError === "This alias is reserved" ||
    presetError === "Subagent aliases must be unique"
      ? presetError
      : undefined
  const forcedOpen = Boolean(rowErrors) || Boolean(eligibilityIssue)
  const expanded = isExpanded || forcedOpen
  const presetSlug = preset?.slug ?? subagent.preset
  const presetName = preset?.name ?? `${subagent.preset} unavailable`
  const alias = subagent.name.trim() || presetSlug

  function handleOpenAgent() {
    if (preset) {
      window.open(
        `/workspaces/${preset.workspace_id}/agents/${preset.id}`,
        "_blank",
        "noopener,noreferrer"
      )
    }
  }

  return (
    <div className="border-b border-border/50">
      <div className="group relative flex min-w-0 items-center gap-2 pl-4 pr-3 hover:bg-muted/50">
        <button
          type="button"
          className="after:absolute after:inset-0 after:content-[''] flex shrink-0 items-center gap-2 rounded-sm py-2.5 disabled:cursor-default disabled:text-muted-foreground focus-visible:outline-none focus-visible:ring-1 focus-visible:ring-inset focus-visible:ring-ring"
          aria-label={`${presetName} ${alias}`}
          aria-expanded={expanded}
          aria-controls={expanded ? bodyId : undefined}
          aria-disabled={forcedOpen}
          disabled={forcedOpen}
          onClick={() => setIsExpanded(!expanded)}
        >
          <ChevronRight
            className={cn(
              "size-4 shrink-0 text-muted-foreground",
              expanded && "rotate-90"
            )}
          />
          <MousePointerClickIcon className="size-4 shrink-0 text-primary" />
        </button>
        {preset ? (
          <button
            type="button"
            className="relative z-10 min-w-0 shrink truncate rounded-sm text-left font-medium underline-offset-2 hover:underline focus-visible:outline-none focus-visible:ring-1 focus-visible:ring-inset focus-visible:ring-ring"
            onClick={handleOpenAgent}
          >
            {presetName}
          </button>
        ) : (
          <span className="min-w-0 shrink truncate font-medium text-muted-foreground">
            {presetName}
          </span>
        )}
        {preset?.model_name ? (
          <Badge
            variant="secondary"
            className="h-5 shrink-0 gap-1 px-2 text-[10px] font-normal"
          >
            <ProviderIcon
              providerId={getModelProviderIconId(preset.model_provider)}
              className="size-3 shrink-0 rounded-none bg-transparent p-0"
            />
            {preset.model_name}
          </Badge>
        ) : null}
        <span className="flex min-w-0 flex-1 items-center gap-2 self-stretch text-left">
          <span className="truncate font-mono text-[10px] text-muted-foreground">
            {alias}
          </span>
          <span className="min-w-0 flex-1 truncate text-muted-foreground">
            {subagent.description}
          </span>
        </span>
        {presetError && !presetAliasError ? (
          <FormField
            control={form.control}
            name={`subagents.${index}.preset`}
            render={() => (
              <FormItem className="space-y-0">
                <FormMessage />
              </FormItem>
            )}
          />
        ) : null}
        <Button
          type="button"
          variant="ghost"
          size="icon"
          className="relative z-10 size-6 shrink-0 border border-transparent hover:border-rose-500 hover:bg-transparent hover:text-rose-500 text-muted-foreground opacity-0 group-hover:opacity-100 focus-visible:opacity-100 focus-visible:ring-1 focus-visible:ring-inset focus-visible:ring-ring"
          onClick={() => onRemove(index)}
          disabled={isSaving}
          aria-label={`Remove ${presetName}`}
        >
          <Minus className="size-3.5" />
        </Button>
      </div>
      {expanded ? (
        <div id={bodyId} className="space-y-3 px-4 pt-3 pb-4">
          {eligibilityIssue ? (
            <Alert variant="destructive" className="text-xs">
              <AlertCircle className="size-4" />
              <AlertTitle>Cannot attach this preset</AlertTitle>
              <AlertDescription>{eligibilityIssue.message}</AlertDescription>
            </Alert>
          ) : null}
          <FormField
            control={form.control}
            name={`subagents.${index}.name`}
            render={({ field }) => (
              <FormItem className="flex items-start gap-3 space-y-0">
                <FormLabel className="w-32 shrink-0 pt-2 text-xs font-normal text-muted-foreground">
                  Alias
                </FormLabel>
                <div className="min-w-0 flex-1">
                  <FormControl>
                    <Input
                      {...field}
                      placeholder={presetSlug}
                      disabled={isSaving}
                      className="h-8 max-w-60 font-mono shadow-none"
                    />
                  </FormControl>
                  <FormMessage>{presetAliasError}</FormMessage>
                </div>
              </FormItem>
            )}
          />
          <FormField
            control={form.control}
            name={`subagents.${index}.description`}
            render={({ field }) => (
              <FormItem className="flex items-start gap-3 space-y-0">
                <FormLabel className="w-32 shrink-0 pt-2 text-xs font-normal text-muted-foreground">
                  Delegate when
                </FormLabel>
                <div className="min-w-0 flex-1">
                  <FormControl>
                    <Textarea
                      {...field}
                      placeholder="When should the parent delegate to this subagent?"
                      disabled={isSaving}
                      rows={2}
                      className="min-h-[52px] shadow-none"
                    />
                  </FormControl>
                  <FormMessage />
                </div>
              </FormItem>
            )}
          />
          <FormField
            control={form.control}
            name={`subagents.${index}.maxTurns`}
            render={({ field }) => (
              <FormItem className="flex items-start gap-3 space-y-0">
                <FormLabel className="w-32 shrink-0 pt-2 text-xs font-normal text-muted-foreground">
                  Max turns
                </FormLabel>
                <div className="min-w-0 flex-1">
                  <FormControl>
                    <Input
                      {...field}
                      type="number"
                      min={1}
                      placeholder="No limit"
                      disabled={isSaving}
                      className="h-8 w-24 shadow-none"
                    />
                  </FormControl>
                  <FormMessage />
                </div>
              </FormItem>
            )}
          />
        </div>
      ) : null}
    </div>
  )
}

/** Edit skill bindings while showing version metadata from the saved preset. */
export function AgentPresetSkillsPanel({
  form,
  workspaceId,
  isSaving,
  skillFields,
  savedBindings,
  onAddSkillBinding,
  onRemoveSkillBinding,
}: {
  form: UseFormReturn<AgentPresetFormValues>
  workspaceId: string
  isSaving: boolean
  skillFields: Array<{ id: string }>
  savedBindings?: AgentPresetRead["skills"]
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
  let addSkillDisabledReason: string | null = null
  if (!skillsLoading && !skillsError && availableSkillsToAdd.length === 0) {
    if (!skills?.length) {
      addSkillDisabledReason = "No skills in this workspace yet"
    } else if (skills.some((skill) => !attachedSkillIds.has(skill.id))) {
      addSkillDisabledReason =
        "Only skills with published versions can be attached."
    } else {
      addSkillDisabledReason =
        "All workspace skills are already attached to this preset."
    }
  }
  const addSkillButton = (
    <Button
      type="button"
      size="sm"
      variant="outline"
      className="h-7 gap-1.5 text-xs shadow-none focus-visible:ring-1 focus-visible:ring-inset focus-visible:ring-ring"
      onClick={() => setIsPickerOpen(true)}
      disabled={isSaving || skillsLoading || availableSkillsToAdd.length === 0}
    >
      <Plus className="size-3.5" />
      Add skill
    </Button>
  )

  function handleAddSkill(skillId: string) {
    onAddSkillBinding({ skillId })
    setIsPickerOpen(false)
  }

  function renderSkills() {
    if (skillsError) {
      return (
        <Alert variant="destructive" className="m-4 w-auto">
          <AlertCircle className="size-4" />
          <AlertTitle>Unable to load skills</AlertTitle>
          <AlertDescription>
            {getApiErrorDetail(skillsError) ?? "Please try again."}
          </AlertDescription>
        </Alert>
      )
    }
    if (skillsLoading) {
      return (
        <p className="px-4 py-3 text-muted-foreground">Loading skills...</p>
      )
    }
    if (skillFields.length === 0) {
      return (
        <p className="px-4 py-3 text-muted-foreground">
          No skills attached yet.
        </p>
      )
    }
    return skillFields.map((item, index) => (
      <AgentPresetSkillBindingRow
        key={item.id}
        form={form}
        workspaceId={workspaceId}
        index={index}
        isSaving={isSaving}
        availableSkills={skills ?? []}
        savedBinding={savedBindings?.find(
          (binding) => binding.skill_id === selectedSkills[index]?.skillId
        )}
        onRemove={onRemoveSkillBinding}
      />
    ))
  }

  return (
    <div className="h-full overflow-auto pb-20 text-xs">
      <div className="flex h-14 items-center gap-2 border-b border-border/50 px-4">
        <h3 className="font-medium">Skills</h3>
        <span className="text-muted-foreground">{skillFields.length}</span>
        <div className="ml-auto">
          {addSkillDisabledReason ? (
            <Tooltip>
              <TooltipTrigger asChild>
                <span
                  className="inline-flex focus-visible:outline-none focus-visible:ring-1 focus-visible:ring-inset focus-visible:ring-ring"
                  tabIndex={0}
                >
                  {addSkillButton}
                </span>
              </TooltipTrigger>
              <TooltipContent>{addSkillDisabledReason}</TooltipContent>
            </Tooltip>
          ) : (
            addSkillButton
          )}
        </div>
      </div>
      {renderSkills()}
      <CommandDialog open={isPickerOpen} onOpenChange={setIsPickerOpen}>
        <DialogDescription className="sr-only">
          Choose a published skill to attach.
        </DialogDescription>
        <CommandInput placeholder="Search skills..." />
        <CommandList>
          <CommandEmpty>No skills found.</CommandEmpty>
          <CommandGroup heading="Workspace skills">
            {availableSkillsToAdd.map((skill) => (
              <CommandItem
                key={skill.id}
                value={buildSkillCommandItemValue(skill)}
                disabled={isSaving}
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
  savedBinding,
  onRemove,
}: {
  form: UseFormReturn<AgentPresetFormValues>
  workspaceId: string
  index: number
  isSaving: boolean
  availableSkills: SkillReadMinimal[]
  savedBinding?: NonNullable<AgentPresetRead["skills"]>[number]
  onRemove: (index: number) => void
}) {
  const selectedSkillId = form.watch(`skills.${index}.skillId`)
  const selectedSkill = availableSkills.find(
    (skill) => skill.id === selectedSkillId
  )
  const displaySkillName =
    selectedSkill?.name ?? savedBinding?.skill_name ?? "Unknown skill"
  const displaySkillDescription = selectedSkill?.description?.trim()

  function handleOpenSkill() {
    if (selectedSkillId) {
      window.open(
        `/workspaces/${workspaceId}/skills/${selectedSkillId}`,
        "_blank",
        "noopener,noreferrer"
      )
    }
  }

  return (
    <div className="group flex h-9 min-w-0 items-center gap-3 border-b border-border/50 px-4 hover:bg-muted/50">
      <Pyramid className="size-4 shrink-0 text-primary" />
      <div className="min-w-0 flex-1">
        <HoverCard>
          <HoverCardTrigger asChild>
            <button
              type="button"
              className="max-w-full truncate rounded-sm text-left underline-offset-2 hover:underline focus-visible:outline-none focus-visible:ring-1 focus-visible:ring-inset focus-visible:ring-ring"
              onClick={handleOpenSkill}
              disabled={!selectedSkillId}
            >
              {displaySkillName}
            </button>
          </HoverCardTrigger>
          {displaySkillDescription ? (
            <HoverCardContent
              align="start"
              className="w-96 max-w-[calc(100vw-2rem)] space-y-1 p-3 text-xs shadow-none"
            >
              <p className="font-medium">{displaySkillName}</p>
              <p className="text-muted-foreground">{displaySkillDescription}</p>
            </HoverCardContent>
          ) : null}
        </HoverCard>
      </div>
      <Button
        type="button"
        variant="ghost"
        size="icon"
        className="size-6 shrink-0 border border-transparent hover:border-rose-500 hover:bg-transparent hover:text-rose-500 text-muted-foreground opacity-0 group-hover:opacity-100 group-focus-within:opacity-100 focus-visible:opacity-100 focus-visible:ring-1 focus-visible:ring-inset focus-visible:ring-ring"
        onClick={() => onRemove(index)}
        disabled={isSaving}
        aria-label={`Remove ${displaySkillName}`}
      >
        <Minus className="size-3.5" />
      </Button>
      {savedBinding?.skill_version != null ? (
        <Tooltip>
          <TooltipTrigger asChild>
            <Badge
              variant="secondary"
              tabIndex={0}
              className="h-5 shrink-0 px-2 font-mono text-[10px] font-normal focus-visible:outline-none focus-visible:ring-1 focus-visible:ring-inset focus-visible:ring-ring"
            >
              v{savedBinding.skill_version}
            </Badge>
          </TooltipTrigger>
          <TooltipContent>Runs the latest published version</TooltipContent>
        </Tooltip>
      ) : null}
    </div>
  )
}

/** Configure text, primitive, or JSON-schema output without changing its stored shape. */
export function AgentPresetStructuredOutputPanel({
  form,
  isSaving,
}: {
  form: UseFormReturn<AgentPresetFormValues>
  isSaving: boolean
}) {
  const outputTypeKind = form.watch("outputTypeKind")
  const outputTypeDataType = form.watch("outputTypeDataType")
  const rememberedDataType = useRef(outputTypeDataType)
  const { type, isList } = parseAgentOutputType(outputTypeDataType)

  return (
    <div className="h-full overflow-auto px-4 pt-2 pb-20 text-xs">
      <FormField
        control={form.control}
        name="outputTypeKind"
        render={({ field }) => (
          <FormItem className="flex min-h-11 items-start gap-3 space-y-0 py-2">
            <FormLabel className="w-32 shrink-0 pt-1.5 text-xs font-normal text-muted-foreground">
              Format
            </FormLabel>
            <div className="min-w-0">
              <FormControl>
                <ToggleTabs<AgentPresetFormValues["outputTypeKind"]>
                  value={field.value}
                  onValueChange={(value) => {
                    if (
                      value === "data-type" &&
                      !form.getValues("outputTypeDataType")
                    ) {
                      form.setValue(
                        "outputTypeDataType",
                        rememberedDataType.current || "str",
                        {
                          shouldDirty: true,
                          shouldValidate: true,
                        }
                      )
                    }
                    if (
                      value !== "data-type" &&
                      form.formState.defaultValues?.outputTypeKind !==
                        "data-type"
                    ) {
                      rememberedDataType.current =
                        form.getValues("outputTypeDataType") ||
                        rememberedDataType.current
                      form.resetField("outputTypeDataType")
                    }
                    field.onChange(value)
                  }}
                  onBlur={field.onBlur}
                  options={[
                    { value: "none", content: "Text" },
                    { value: "data-type", content: "Structured" },
                    { value: "json", content: "JSON schema" },
                  ]}
                  aria-label="Format"
                  role="group"
                  size="sm"
                  className="[&_button:focus-visible]:ring-1 [&_button:focus-visible]:ring-inset [&_button:focus-visible]:ring-ring"
                  disabled={isSaving}
                />
              </FormControl>
              <FormMessage />
            </div>
          </FormItem>
        )}
      />
      {outputTypeKind === "data-type" ? (
        <>
          <FormField
            control={form.control}
            name="outputTypeDataType"
            render={({ field }) => (
              <FormItem className="flex min-h-11 items-start gap-3 space-y-0 py-2">
                <FormLabel className="w-32 shrink-0 pt-1.5 text-xs font-normal text-muted-foreground">
                  Type
                </FormLabel>
                <div className="min-w-0">
                  <FormControl>
                    <ToggleTabs<AgentOutputPrimitive | "">
                      value={type}
                      onValueChange={(value) =>
                        field.onChange(
                          value
                            ? formatAgentOutputType({ type: value, isList })
                            : ""
                        )
                      }
                      onBlur={field.onBlur}
                      options={[
                        { value: "str", content: "String" },
                        { value: "int", content: "Integer" },
                        { value: "float", content: "Float" },
                        { value: "bool", content: "Boolean" },
                      ]}
                      aria-label="Type"
                      role="group"
                      size="sm"
                      className="[&_button:focus-visible]:ring-1 [&_button:focus-visible]:ring-inset [&_button:focus-visible]:ring-ring"
                      disabled={isSaving}
                    />
                  </FormControl>
                  <FormMessage />
                </div>
              </FormItem>
            )}
          />
          <FormField
            control={form.control}
            name="outputTypeDataType"
            render={({ field }) => (
              <FormItem className="flex min-h-11 items-center gap-3 space-y-0 py-2">
                <FormLabel className="w-32 shrink-0 text-xs font-normal text-muted-foreground">
                  List of values
                </FormLabel>
                <FormControl>
                  <Switch
                    checked={isList}
                    onCheckedChange={(checked) =>
                      field.onChange(
                        type
                          ? formatAgentOutputType({ type, isList: checked })
                          : ""
                      )
                    }
                    onBlur={field.onBlur}
                    disabled={isSaving || !type}
                    className="shadow-none focus-visible:ring-1 focus-visible:ring-inset focus-visible:ring-ring [&_span]:shadow-none"
                  />
                </FormControl>
              </FormItem>
            )}
          />
          <div className="flex min-h-11 items-center gap-3">
            <span className="w-32 shrink-0 text-muted-foreground">Returns</span>
            <output className="font-mono">{outputTypeDataType}</output>
          </div>
        </>
      ) : null}
      {outputTypeKind === "json" ? (
        <FormField
          control={form.control}
          name="outputTypeJson"
          render={({ field }) => (
            <FormItem className="flex items-start gap-3 space-y-0 py-2">
              <FormLabel className="w-32 shrink-0 pt-2 text-xs font-normal text-muted-foreground">
                JSON schema
              </FormLabel>
              <div className="min-w-0 flex-1">
                <FormControl>
                  <CodeEditor
                    value={field.value ?? ""}
                    onChange={field.onChange}
                    language="json"
                    readOnly={isSaving}
                    className="min-h-[200px]"
                  />
                </FormControl>
                <FormMessage />
              </div>
            </FormItem>
          )}
        />
      ) : null}
    </div>
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
  const { user } = useAuth()
  const presetId = preset?.id
  const [selectedChatId, setSelectedChatId] = useState<string | null>(null)
  const [historyScope, setHistoryScope] = useState<ChatHistoryScope>("team")
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
      createdBy: historyScope === "mine" ? user?.id : undefined,
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

  const latestChatId =
    chats?.find((candidate) => !candidate.is_readonly)?.id ?? chats?.[0]?.id
  const activeChatId = selectedChatId ?? latestChatId

  const handleHistoryScopeChange = (nextScope: ChatHistoryScope) => {
    setHistoryScope(nextScope)
    setSelectedChatId(null)
  }

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

    if ((!chatReady || !modelInfo) && !chat?.is_readonly) {
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

    if (
      chatLoading ||
      chatsLoading ||
      !chat ||
      (!modelInfo && !chat.is_readonly)
    ) {
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
        modelInfo={modelInfo ?? undefined}
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
              workspaceId={workspaceId}
              scope={historyScope}
              onScopeChange={handleHistoryScopeChange}
              align="end"
            />
            <Button
              type="button"
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
  const subagents = (agents?.subagents ?? []).map((subagent) => ({
    preset: subagent.preset,
    presetId: "preset_id" in subagent ? subagent.preset_id : "",
    presetVersionId:
      "preset_version_id" in subagent ? subagent.preset_version_id : "",
    name: subagent.name ?? "",
    description: subagent.description ?? "",
    maxTurns:
      subagent.max_turns === null || subagent.max_turns === undefined
        ? ""
        : String(subagent.max_turns),
  }))

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
    subagents,
    skills:
      preset.skills?.map(
        (binding): SkillBindingFormValue => ({
          skillId: binding.skill_id,
        })
      ) ?? [],
    retries: preset.retries ?? DEFAULT_RETRIES,
    enableThinking: preset.enable_thinking ?? true,
    enableInternetAccess: preset.enable_internet_access ?? false,
  }
}

/** Convert editor values to the API payload, preserving explicit approval choices. */
export function formValuesToPayload(
  values: AgentPresetFormValues,
  options?: { forceInternetAccess?: boolean }
): AgentPresetCreate {
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
    })),
    tool_approvals: toToolApprovalMap(values.toolApprovals),
    retries: values.retries,
    enable_thinking: values.enableThinking,
    enable_internet_access:
      values.enableInternetAccess || options?.forceInternetAccess === true,
  }
}

function formValuesToAgentsPayload(
  values: AgentPresetFormValues
): AgentPresetCreate["agents"] {
  const subagents = values.subagents
    .map((subagent): AnyAttachedSubagentRef | null => {
      const preset = subagent.preset.trim()
      if (!preset) {
        return null
      }

      const name = normalizeOptional(subagent.name)
      const description = normalizeOptional(subagent.description)
      const maxTurns = parseOptionalPositiveInteger(subagent.maxTurns)
      const presetId = normalizeOptional(subagent.presetId)
      const presetVersionId = normalizeOptional(subagent.presetVersionId)

      // Keep the immutable identifiers when we know both, so the parent
      // stays bound to the exact child preset version instead of
      // re-resolving a slug that may have been renamed or reused.
      const payload: AnyAttachedSubagentRef =
        presetId !== null && presetVersionId !== null
          ? { preset, preset_id: presetId, preset_version_id: presetVersionId }
          : { preset }

      if (name !== null) {
        payload.name = name
      }
      if (description !== null) {
        payload.description = description
      }
      if (maxTurns !== null) {
        payload.max_turns = maxTurns
      }
      return payload
    })
    .filter((subagent): subagent is AnyAttachedSubagentRef => subagent !== null)

  return {
    subagents,
  }
}

function normalizeOptional(value: string | null | undefined) {
  if (value == null) {
    return null
  }
  const trimmed = value.trim()
  return trimmed.length > 0 ? trimmed : null
}

function getSubagentFormAlias(subagent: SubagentFormValue): string {
  return subagent.name.trim() || subagent.preset.trim()
}

function getFirstSubagentEligibilityIssue({
  subagents,
  presetsById,
  presetsBySlug,
}: {
  subagents: SubagentFormValue[]
  presetsById: Map<string, AgentPresetReadMinimal>
  presetsBySlug: Map<string, AgentPresetReadMinimal>
}): (SubagentEligibilityIssue & { index: number }) | null {
  for (const [index, subagent] of subagents.entries()) {
    const preset = getSubagentPreset({
      subagent,
      presetsById,
      presetsBySlug,
    })
    if (preset === null) {
      continue
    }
    const issue = getSubagentEligibilityIssue({ preset })
    if (issue) {
      return { ...issue, index }
    }
  }
  return null
}

function getSubagentPreset({
  subagent,
  presetsById,
  presetsBySlug,
}: {
  subagent: SubagentFormValue
  presetsById: Map<string, AgentPresetReadMinimal>
  presetsBySlug: Map<string, AgentPresetReadMinimal>
}): AgentPresetReadMinimal | null {
  const presetId = normalizeOptional(subagent.presetId)
  if (presetId !== null) {
    return presetsById.get(presetId) ?? null
  }
  return presetsBySlug.get(subagent.preset.trim()) ?? null
}

type SubagentEligibilityIssue = {
  field: "preset"
  message: string
}

function getSubagentEligibilityIssue({
  preset,
}: {
  preset: AgentPresetReadMinimal
}): SubagentEligibilityIssue | null {
  const message = getSubagentEligibilityMessage(
    preset.current_version_subagent_eligibility
  )
  return message ? { field: "preset", message } : null
}

function getSubagentEligibilityMessage(
  eligibility: AgentPresetSubagentEligibility | null | undefined
): string | null {
  if (!eligibility || eligibility.eligible) {
    return null
  }
  return eligibility.message ?? "This version cannot be attached as a subagent."
}

function getOrderedAgentPresetCapabilities(
  preset: AgentPresetReadMinimal
): AgentPresetCapability[] {
  const capabilities = new Set(preset.capabilities ?? [])
  return AGENT_PRESET_CAPABILITY_CONFIG.filter(({ capability }) =>
    capabilities.has(capability)
  ).map(({ capability }) => capability)
}

function getAgentPresetCapabilityConfigs(
  capabilities: AgentPresetCapability[]
) {
  const capabilitySet = new Set(capabilities)
  return AGENT_PRESET_CAPABILITY_CONFIG.filter(({ capability }) =>
    capabilitySet.has(capability)
  )
}

function formatAgentPresetCapabilityLabels(
  capabilities: AgentPresetCapability[]
): string {
  return getAgentPresetCapabilityConfigs(capabilities)
    .map(({ label }) => label)
    .join(", ")
}

function AgentPresetCapabilityIcons({
  capabilities,
}: {
  capabilities: AgentPresetCapability[]
}) {
  const configs = getAgentPresetCapabilityConfigs(capabilities)
  if (configs.length === 0) {
    return null
  }
  const label = formatAgentPresetCapabilityLabels(capabilities)

  return (
    <span
      className="ml-auto flex shrink-0 items-center gap-1 text-muted-foreground"
      aria-label={label}
      title={label}
    >
      {configs.map(({ capability, Icon }) => (
        <Icon key={capability} className="!size-3.5" aria-hidden="true" />
      ))}
    </span>
  )
}

function getSelectedInternetAccessSubagentAliases({
  subagents,
  presetsById,
  presetsBySlug,
}: {
  subagents: SubagentFormValue[]
  presetsById: Map<string, AgentPresetReadMinimal>
  presetsBySlug: Map<string, AgentPresetReadMinimal>
}): string[] {
  const aliases: string[] = []

  for (const subagent of subagents) {
    const preset = getSubagentPreset({
      subagent,
      presetsById,
      presetsBySlug,
    })
    if (preset === null) {
      continue
    }
    const capabilities = preset.capabilities ?? []
    if (capabilities.includes("internet_access")) {
      aliases.push(getSubagentFormAlias(subagent))
    }
  }

  return aliases
}

function getInternetAccessWarningMessage({
  parentInternetAccessEnabled,
  selectedInternetAccessSubagentAliases,
}: {
  parentInternetAccessEnabled: boolean
  selectedInternetAccessSubagentAliases: string[]
}): string | null {
  if (
    parentInternetAccessEnabled ||
    selectedInternetAccessSubagentAliases.length === 0
  ) {
    return null
  }
  return LIVE_INTERNET_ACCESS_WARNING_MESSAGE
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
