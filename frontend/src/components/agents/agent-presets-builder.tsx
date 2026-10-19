"use client"

import { zodResolver } from "@hookform/resolvers/zod"
import { DotFilledIcon } from "@radix-ui/react-icons"
import {
  AlertCircle,
  ChevronDown,
  ChevronLeft,
  ChevronRight,
  Globe,
  Loader2,
  type LucideIcon,
  MessageCircle,
  Minus,
  MousePointerClickIcon,
  PanelRight,
  Plus,
  Pyramid,
  ShieldCheck,
  X,
} from "lucide-react"
import Link from "next/link"
import { useRouter, useSearchParams } from "next/navigation"
import {
  type FormEvent,
  type ReactNode,
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
  LibrarySkillRead,
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
import {
  LogoTile,
  RAIL_LIST_ROW,
  RAIL_REMOVE_BUTTON,
  RAIL_SURFACE,
  RailEmpty,
  RailSectionHeader,
} from "@/components/agents/agent-preset-rail-primitives"
import {
  AgentPresetRailToolGroup,
  AgentPresetRailTools,
} from "@/components/agents/agent-preset-rail-tools"
import { AgentPresetVersionSelect } from "@/components/agents/agent-preset-version-select"
import { SlackChannelPanel } from "@/components/agents/external-channels/slack-channel-panel"
import { useScopeCheck } from "@/components/auth/scope-guard"
import {
  ChatHistoryDropdown,
  type ChatHistoryScope,
} from "@/components/chat/chat-history-dropdown"
import {
  AGENT_EXECUTE_REQUIRED_MESSAGE,
  ChatSessionPane,
} from "@/components/chat/chat-session-pane"
import { CodeEditor } from "@/components/editor/codemirror/code-editor"
import { getModelProviderIconId, ProviderIcon } from "@/components/icons"
import { CenteredSpinner } from "@/components/loading/spinner"
import { SkillLibraryProviderIcon } from "@/components/skills/skill-library-provider-icon"
import { SimpleEditor } from "@/components/tiptap-templates/simple/simple-editor"
import { Alert, AlertDescription, AlertTitle } from "@/components/ui/alert"
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
  DropdownMenu,
  DropdownMenuCheckboxItem,
  DropdownMenuContent,
  DropdownMenuRadioGroup,
  DropdownMenuRadioItem,
  DropdownMenuSeparator,
  DropdownMenuSub,
  DropdownMenuSubContent,
  DropdownMenuSubTrigger,
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
import { useSkillLibrary } from "@/hooks/use-skill-library"
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
  useListMcpIntegrations,
  useRegistryActions,
  useWorkspaceAgentModels,
} from "@/lib/hooks"
import { libraryProviderSlug, librarySkillPath } from "@/lib/skill-library"
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
    librarySkills: z.array(z.string()).default([]),
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
  librarySkills: [],
  toolApprovals: [],
  retries: DEFAULT_RETRIES,
  enableThinking: true,
  enableInternetAccess: false,
}

export function AgentPresetsBuilder({ presetId }: { presetId?: string }) {
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
  const { models } = useWorkspaceAgentModels(workspaceId)
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
    () => buildEnabledModelOptions(models),
    [models]
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
        initialTab={queryTab ?? "configuration"}
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
  const { models } = useWorkspaceAgentModels(workspaceId)
  const enabledModelsLoaded = models !== undefined
  const { mcpIntegrations, mcpIntegrationsError } =
    useListMcpIntegrations(workspaceId)
  const { updateAgentPreset, updateAgentPresetIsPending } =
    useUpdateAgentPreset(workspaceId)

  const enabledModelOptions = useMemo(
    () => buildEnabledModelOptions(models),
    [models]
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

/** Shown in place of a chat when the user's role lacks `agent:execute`. */
function AgentExecuteRequiredNotice() {
  return (
    <div className="flex h-full flex-col items-center justify-center gap-2 px-4 text-center text-xs text-muted-foreground">
      <AlertCircle className="size-5 text-amber-500" />
      <p className="max-w-xs text-pretty">{AGENT_EXECUTE_REQUIRED_MESSAGE}</p>
    </div>
  )
}

function AgentPresetChatPane({
  preset,
  workspaceId,
  enabledModelOptions,
  enabledModelsLoaded,
  leading,
  trailing,
}: {
  preset: AgentPresetRead | null
  workspaceId: string
  enabledModelOptions: EnabledModelOption[]
  enabledModelsLoaded: boolean
  /** Rendered at the start of the pane bar (the mode switch). */
  leading: ReactNode
  /** Rendered at the end of the pane bar (the setup toggle). */
  trailing: ReactNode
}) {
  const { user } = useAuth()
  // `undefined` while scopes load: creation waits, nothing is disabled.
  const canExecuteAgents = useScopeCheck("agent:execute")
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
    canExecuteAgents === true &&
      preset &&
      effectiveModelConfig &&
      (!enabledModelsLoaded || selectedModel !== null || hasLegacyModelConfig)
  )
  const shouldAutoCreateChat =
    canStartChat && !activeChatId && !chatsLoading && !createChatPending

  const handleCreateChat = async () => {
    if (!preset || createChatPending || canExecuteAgents !== true) {
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
    // Changing the pinned version updates the session, which needs agent:execute.
    if (!activeChatId || chat?.is_readonly || canExecuteAgents !== true) return
    try {
      await updateChat({
        chatId: activeChatId,
        update: { agent_preset_version_id: nextVersionId },
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

    if (
      canExecuteAgents === false &&
      !activeChatId &&
      !chatsLoading &&
      !chatsError
    ) {
      return <AgentExecuteRequiredNotice />
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
        emptyState={<PlaygroundEmptyState />}
        toolsEnabled={false}
        centered
      />
    )
  }

  return (
    <div className="flex h-full flex-col bg-background">
      <div className="flex h-12 shrink-0 items-center gap-1 border-b border-border/60 px-5">
        {leading}
        <span className="flex-1" />
        {preset ? (
          <AgentPresetVersionSelect
            versions={versions}
            versionsIsLoading={versionsIsLoading}
            versionsError={versionsError}
            selectedVersionId={selectedVersionId}
            currentVersionId={preset.current_version_id ?? null}
            onSelect={handlePresetVersionChange}
            disabled={
              !activeChatId ||
              !chat ||
              chat.is_readonly ||
              isUpdating ||
              canExecuteAgents !== true
            }
            triggerClassName="h-7 w-[10.5rem] text-xs shadow-none"
          />
        ) : null}
        {preset ? (
          <>
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
            <span aria-hidden="true" className="mx-1 h-4 w-px bg-border" />
          </>
        ) : null}
        {trailing}
      </div>
      <div className="flex-1 min-h-0">{renderBody()}</div>
    </div>
  )
}

function PlaygroundEmptyState() {
  return (
    <div className="max-w-sm text-center">
      <p className="text-sm font-medium text-foreground">Try out your agent</p>
      <p className="mt-1 text-pretty text-sm text-muted-foreground">
        Send a message to see how it responds and which tools it calls. Runs use
        the last saved version.
      </p>
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

  if (errors.skills || errors.librarySkills) {
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
    return "assistant"
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
  librarySkills: "library_skills",
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
    return "Create agent"
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
      return "Save version"
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
  initialTab = "configuration",
  onTabChange,
}: AgentPresetFormProps) {
  const [leftMode, setLeftMode] = useState<AgentPresetLeftMode>(() =>
    tabToLeftMode(initialTab)
  )
  // null follows the mode: open for Instructions, hidden in Playground.
  const [railPinned, setRailPinned] = useState<boolean | null>(null)
  const railOpen = railPinned ?? leftMode === "instructions"
  const [railTab, setRailTab] = useState<AgentPresetRailTab>(() =>
    tabToRailTab(initialTab)
  )
  const [railDrill, setRailDrill] = useState<AgentPresetRailDrill | null>(null)
  const { hasEntitlement, hasEntitlementData } = useEntitlements()
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
  // Routes a legacy tab target, such as a validation error, to its new home.
  const focusTab = useCallback(
    (tab: AgentPresetSideTab, subagentIndex?: number) => {
      if (tab === "live-chat" || tab === "channels") {
        setLeftMode(tabToLeftMode(tab))
        return
      }
      // Every other target, including the model row, lives in or beside the rail.
      setRailPinned(true)
      if (tab === "assistant") setLeftMode("instructions")
      setRailTab(tabToRailTab(tab))
      setRailDrill(
        tab === "subagents" && subagentIndex !== undefined
          ? { kind: "subagent", index: subagentIndex }
          : null
      )
    },
    []
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

  // Wait for entitlements so a deep link to Channels survives the first render.
  useEffect(() => {
    if (hasEntitlementData && !channelsEnabled && leftMode === "channels") {
      setLeftMode("instructions")
    }
  }, [channelsEnabled, hasEntitlementData, leftMode])

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
        focusTab("subagents", eligibilityIssue.index)
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
      if (nextTab === "subagents" && Array.isArray(errors.subagents)) {
        const index = errors.subagents.findIndex(Boolean)
        focusTab("subagents", index >= 0 ? index : undefined)
      } else if (nextTab) {
        focusTab(nextTab)
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
  const cutsVersion = submitLabel !== "Save changes"
  const isDirty = form.formState.isDirty

  // Everything registered below must have a stable identity, because the
  // registration effect writes to provider state and this component consumes
  // that provider: an unstable dependency re-fires the effect on every render
  // and loops forever. `form.handleSubmit(...)` returns a new function each
  // render, so route the callbacks through refs so the registered object only
  // changes when the values the header actually renders change.
  const handleSubmitRef = useRef(handleSubmit)
  const getDraftPayloadRef = useRef(getDraftPayload)
  const presetRef = useRef(preset)
  useEffect(() => {
    handleSubmitRef.current = handleSubmit
    getDraftPayloadRef.current = getDraftPayload
    presetRef.current = preset
  })
  const discard = useCallback(() => {
    const saved = presetRef.current
    form.reset(saved ? presetToFormValues(saved) : DEFAULT_FORM_VALUES)
  }, [form])
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
      isDirty,
      cutsVersion,
      discard,
    }),
    [
      canSubmit,
      cutsVersion,
      discard,
      isDirty,
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
      isDirty={form.formState.isDirty}
      cutsVersion={cutsVersion}
      onDiscard={discard}
    />
  )

  // Channels lives in the URL so the Slack OAuth redirect returns to it.
  const modeSwitch = (
    <SegmentedControl<AgentPresetLeftMode>
      ariaLabel="Left pane"
      value={leftMode}
      onValueChange={(value) => {
        setLeftMode(value)
        setRailPinned(null)
        onTabChange?.(LEFT_MODE_TABS[value])
      }}
      options={[
        { value: "instructions", label: "Instructions" },
        { value: "playground", label: "Playground" },
        ...(channelsEnabled
          ? [{ value: "channels" as const, label: "Channels" }]
          : []),
      ]}
    />
  )

  const railToggleLabel = railOpen ? "Hide setup" : "Show setup"
  const railToggle = (
    <Tooltip>
      <TooltipTrigger asChild>
        <Button
          type="button"
          variant="ghost"
          size="icon"
          className={cn(
            "size-7 text-muted-foreground",
            railOpen && leftMode === "playground" && "bg-accent"
          )}
          aria-label={railToggleLabel}
          aria-pressed={railOpen}
          onClick={() => setRailPinned(!railOpen)}
        >
          <PanelRight className="size-4" />
        </Button>
      </TooltipTrigger>
      <TooltipContent>{railToggleLabel}</TooltipContent>
    </Tooltip>
  )

  // No detail header (artifact view): keep form actions reachable.
  const inlineActions = detail ? null : (
    <AgentPresetDetailActions
      workspaceId={workspaceId}
      presetId={presetIdValue}
      currentVersionId={presetCurrentVersionId}
      getDraftPayload={getDraftPayloadStable}
      isSaving={isSaving}
      canSubmit={canSubmit}
      submitLabel={submitLabel}
      onPublish={submit}
      isDirty={isDirty}
      cutsVersion={cutsVersion}
      onDiscard={discard}
    />
  )

  let leftPanel: ReactNode
  if (leftMode === "playground") {
    leftPanel = (
      <AgentPresetChatPane
        preset={preset}
        workspaceId={workspaceId}
        enabledModelOptions={enabledModelOptions}
        enabledModelsLoaded={enabledModelsLoaded}
        leading={modeSwitch}
        trailing={
          <>
            {inlineActions}
            {railToggle}
          </>
        }
      />
    )
  } else {
    leftPanel = (
      <div className="flex h-full min-h-0 flex-col">
        <div className="flex h-12 shrink-0 items-center gap-1 border-b border-border/60 px-5">
          {modeSwitch}
          <span className="flex-1" />
          {leftMode === "channels" ? inlineActions : null}
          {railToggle}
        </div>
        <div className="min-h-0 flex-1">
          {/* Mount once entitlements resolve; the panel's early return
              while loading changes its hook order. */}
          {leftMode === "channels" ? (
            channelsEnabled ? (
              <SlackChannelPanel workspaceId={workspaceId} preset={preset} />
            ) : null
          ) : (
            documentPanel
          )}
        </div>
      </div>
    )
  }

  const rail = (
    <AgentPresetRail
      tab={railTab}
      onTabChange={(tab) => {
        setRailTab(tab)
        setRailDrill(null)
      }}
      drill={railDrill}
      onDrillChange={setRailDrill}
      preset={preset}
      workspaceId={workspaceId}
      agentPresets={agentPresets}
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

  const stacked = layout === "stacked"
  // Fixed ids keep the left pane mounted when the rail toggles.
  const body = (
    <ResizablePanelGroup
      direction={stacked ? "vertical" : "horizontal"}
      className="h-full"
    >
      <ResizablePanel
        id="agent-preset-left"
        order={1}
        defaultSize={stacked ? 42 : 64}
        minSize={stacked ? 28 : 40}
        className="overflow-hidden"
      >
        {leftPanel}
      </ResizablePanel>
      {railOpen ? (
        <>
          <ResizableHandle />
          <ResizablePanel
            id="agent-preset-rail"
            order={2}
            defaultSize={stacked ? 58 : 36}
            minSize={stacked ? 32 : 26}
            className="overflow-hidden"
          >
            {rail}
          </ResizablePanel>
        </>
      ) : null}
    </ResizablePanelGroup>
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
        {body}
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
  isDirty,
  cutsVersion,
  onDiscard,
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
  isDirty: boolean
  cutsVersion: boolean
  onDiscard: () => void
}) {
  // Context present -> the standalone route's global controls header renders
  // the detail actions; context absent (case artifact view) -> render them
  // inline next to the title.
  const detail = useAgentPresetDetailContext()
  return (
    <ScrollArea className="h-full [&_[data-radix-scroll-area-viewport]>div]:!flex [&_[data-radix-scroll-area-viewport]>div]:min-h-full [&_[data-radix-scroll-area-viewport]>div]:flex-col">
      <div className="mx-auto flex w-full max-w-[700px] flex-1 flex-col gap-0 px-[clamp(16px,5vw,48px)] pt-12 pb-10">
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
                      className="h-auto w-full border-none bg-transparent px-0 text-[30px] font-semibold leading-tight tracking-[-0.02em] text-foreground shadow-none outline-none transition-none placeholder:text-muted-foreground/40 focus-visible:bg-transparent focus-visible:outline-none focus-visible:ring-0"
                      placeholder="New agent preset"
                      disabled={isSaving}
                      {...field}
                      value={field.value ?? ""}
                    />
                  </FormControl>
                  <FormMessage className="text-xs" />
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
                      className="w-full resize-none overflow-hidden border-none bg-transparent px-0 text-[15px] leading-[1.55] text-muted-foreground shadow-none outline-none transition-none placeholder:text-muted-foreground/50 focus-visible:bg-transparent focus-visible:outline-none focus-visible:ring-0"
                    />
                  </FormControl>
                </FormItem>
              )}
            />
          </div>
          {detail ? null : (
            <div className="flex flex-wrap items-center gap-2">
              <AgentPresetDetailActions
                workspaceId={workspaceId}
                presetId={presetId}
                currentVersionId={currentVersionId}
                getDraftPayload={getDraftPayload}
                isSaving={isSaving}
                canSubmit={canSubmit}
                submitLabel={submitLabel}
                onPublish={onPublish}
                isDirty={isDirty}
                cutsVersion={cutsVersion}
                onDiscard={onDiscard}
              />
            </div>
          )}
        </div>
        <Separator className="my-8" />
        <section className="flex flex-1 flex-col">
          <FormField
            control={form.control}
            name="instructions"
            render={({ field }) => (
              <FormItem className="flex flex-1 flex-col">
                <FormControl>
                  <SimpleEditor
                    value={field.value ?? ""}
                    onChange={field.onChange}
                    onBlur={field.onBlur}
                    placeholder="You are a helpful analyst..."
                    editable={!isSaving}
                    showToolbar={false}
                    className="flex flex-1 flex-col [&_.simple-editor-content]:flex-1 [&_.simple-editor-content_.tiptap.ProseMirror.simple-editor]:min-h-24 [&_.simple-editor-content_.tiptap.ProseMirror.simple-editor]:text-[15.5px] [&_.simple-editor-content_.tiptap.ProseMirror.simple-editor]:leading-[1.7]"
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

/** Two-option segmented control used for the left pane and output format. */
function SegmentedControl<T extends string>({
  ariaLabel,
  value,
  onValueChange,
  options,
  disabled,
  size = "md",
}: {
  ariaLabel: string
  value: T
  onValueChange: (value: T) => void
  options: Array<{ value: T; label: string }>
  disabled?: boolean
  size?: "sm" | "md"
}) {
  return (
    <div
      role="group"
      aria-label={ariaLabel}
      className={cn(
        "flex w-max bg-muted p-0.5 dark:bg-[hsl(0_0%_9%)]",
        size === "sm" ? "rounded-md" : "rounded-lg"
      )}
    >
      {options.map((option) => {
        const active = option.value === value
        return (
          <button
            key={option.value}
            type="button"
            aria-pressed={active}
            disabled={disabled}
            onClick={() => onValueChange(option.value)}
            className={cn(
              size === "sm"
                ? "h-[22px] rounded-[5px] px-2 text-xs"
                : "h-[26px] rounded-md px-3 text-[13px]",
              "transition-colors focus-visible:outline-none focus-visible:ring-1 focus-visible:ring-inset focus-visible:ring-ring disabled:opacity-50",
              active
                ? "bg-background text-foreground shadow-[0_1px_2px_rgba(0,0,0,0.08)] dark:bg-[hsl(0_0%_15%)] dark:shadow-none"
                : "text-muted-foreground hover:text-foreground"
            )}
          >
            {option.label}
          </button>
        )
      })}
    </div>
  )
}

type AgentPresetLeftMode = "instructions" | "playground" | "channels"

const LEFT_MODE_TABS: Record<AgentPresetLeftMode, AgentPresetSideTab> = {
  instructions: "configuration",
  playground: "live-chat",
  channels: "channels",
}

function tabToLeftMode(tab: AgentPresetSideTab): AgentPresetLeftMode {
  if (tab === "live-chat") return "playground"
  if (tab === "channels") return "channels"
  return "instructions"
}

const RAIL_TABS = [
  { value: "tools", label: "Tools" },
  { value: "skills", label: "Skills" },
  { value: "subagents", label: "Subagents" },
  { value: "output", label: "Output" },
] as const

type AgentPresetRailTab = (typeof RAIL_TABS)[number]["value"]

type AgentPresetRailDrill =
  | { kind: "subagent"; index: number }
  | { kind: "tools"; groupKey: string; title: string }

function tabToRailTab(tab: AgentPresetSideTab): AgentPresetRailTab {
  if (tab === "skills") return "skills"
  if (tab === "subagents") return "subagents"
  if (tab === "structured-output") return "output"
  return "tools"
}

function isAgentPresetRailTab(value: string): value is AgentPresetRailTab {
  return RAIL_TABS.some((tab) => tab.value === value)
}

const RAIL_ROW =
  "grid min-h-8 w-full grid-cols-[104px_minmax(0,1fr)] items-center rounded-md px-2 select-none text-left text-[13px] transition-colors hover:bg-foreground/[0.04] focus-visible:outline-none focus-visible:ring-1 focus-visible:ring-inset focus-visible:ring-ring"

/** Tabbed agent configuration with drill-ins. */
function AgentPresetRail({
  tab,
  onTabChange,
  drill,
  onDrillChange,
  preset,
  workspaceId,
  agentPresets,
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
  tab: AgentPresetRailTab
  onTabChange: (tab: AgentPresetRailTab) => void
  drill: AgentPresetRailDrill | null
  onDrillChange: (drill: AgentPresetRailDrill | null) => void
  preset: AgentPresetRead | null
  workspaceId: string
  agentPresets: AgentPresetReadMinimal[]
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
  const subagents = useWatch({ control: form.control, name: "subagents" })
  const savedSkillIds = preset
    ? (preset.skills ?? []).map((skill) => skill.skill_id)
    : undefined
  // The root list and the group drill-in share one live policy.
  const toolPolicy = useLiveToolPolicy({
    workspaceId,
    form,
    savedSkillIds,
    savedNamespaces: preset?.namespaces,
    savedSkillActions: preset?.tool_policy?.skill_actions,
    maxTools: preset?.tool_policy?.max_tools,
  })
  const back = () => onDrillChange(null)
  // Drill-ins belong to the tab that opened them.
  const activeDrill =
    drill?.kind === "subagent" &&
    tab === "subagents" &&
    subagentFields[drill.index] !== undefined
      ? drill
      : drill?.kind === "tools" && tab === "tools"
        ? drill
        : null
  let drillTitle = ""
  if (activeDrill?.kind === "tools") drillTitle = activeDrill.title
  if (activeDrill?.kind === "subagent") {
    const subagent = subagents?.[activeDrill.index]
    drillTitle = subagent
      ? getSubagentFormAlias(subagent) || "Subagent"
      : "Subagent"
  }
  const tabLabel = RAIL_TABS.find((option) => option.value === tab)?.label

  return (
    <aside
      aria-label="Agent configuration"
      className={cn("flex h-full min-h-0 flex-col", RAIL_SURFACE)}
    >
      <Tabs
        value={tab}
        onValueChange={(value) => {
          if (isAgentPresetRailTab(value)) onTabChange(value)
        }}
        className="flex h-full min-h-0 flex-col"
      >
        <div className="flex h-12 shrink-0 items-stretch border-b border-border/60 px-3">
          <TabsList className="h-full gap-1 rounded-none bg-transparent p-0">
            {RAIL_TABS.map((option) => (
              <TabsTrigger
                key={option.value}
                value={option.value}
                className="h-full rounded-none px-2 text-xs font-normal data-[state=active]:bg-transparent data-[state=active]:text-foreground"
              >
                {option.label}
              </TabsTrigger>
            ))}
          </TabsList>
        </div>
        <ScrollArea className="min-h-0 flex-1 [&_[data-radix-scroll-area-viewport]>div]:!block">
          <div className="px-4 pt-4 pb-6 text-xs">
            {activeDrill ? (
              <div>
                <div className="mb-3 flex h-8 items-center gap-1">
                  <Button
                    type="button"
                    variant="ghost"
                    size="sm"
                    className="h-7 gap-1 px-2 text-[13px] font-normal text-muted-foreground"
                    onClick={back}
                  >
                    <ChevronLeft className="size-3.5" />
                    {tabLabel}
                  </Button>
                  <span className="text-muted-foreground/60">/</span>
                  <span className="truncate px-1.5 text-[13px] text-foreground">
                    {drillTitle}
                  </span>
                </div>
                {activeDrill.kind === "tools" ? (
                  <AgentPresetRailToolGroup
                    groupKey={activeDrill.groupKey}
                    registryActions={registryActions}
                    mcpIntegrations={mcpIntegrations}
                    registryLoading={registryLoading}
                    mcpLoading={mcpLoading}
                    toolsLoadError={toolsLoadError}
                    isSaving={isSaving}
                    maxTools={toolPolicy.maxTools}
                    skillActions={toolPolicy.skillActions}
                    onEmptied={back}
                  />
                ) : (
                  <AgentPresetSubagentDetail
                    key={subagentFields[activeDrill.index]?.id}
                    form={form}
                    index={activeDrill.index}
                    isSaving={isSaving}
                    parentPreset={preset}
                    agentPresets={agentPresets}
                    onRemove={(index) => {
                      onRemoveSubagent(index)
                      back()
                    }}
                  />
                )}
              </div>
            ) : (
              <>
                <TabsContent value="tools" className="mt-0">
                  <div className="flex flex-col gap-6">
                    <AgentPresetSettingsRows
                      form={form}
                      isSaving={isSaving}
                      enabledModelOptions={enabledModelOptions}
                      enabledModelsLoaded={enabledModelsLoaded}
                      hasStdioMcp={hasStdioMcp}
                      onOpenOutput={() => onTabChange("output")}
                    />
                    <AgentPresetConfigurationPanel
                      workspaceId={workspaceId}
                      savedSkillIds={savedSkillIds}
                      maxTools={preset?.tool_policy?.max_tools}
                      savedSkillActions={preset?.tool_policy?.skill_actions}
                      savedNamespaces={preset?.namespaces}
                      form={form}
                      isSaving={isSaving}
                      registryActions={registryActions}
                      registryLoading={registryLoading}
                      mcpLoading={mcpLoading}
                      toolsLoadError={toolsLoadError}
                      mcpIntegrations={mcpIntegrations}
                      onOpenGroup={(groupKey, title) =>
                        onDrillChange({ kind: "tools", groupKey, title })
                      }
                    />
                  </div>
                </TabsContent>
                <TabsContent value="skills" className="mt-0">
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
                <TabsContent value="subagents" className="mt-0">
                  <AgentPresetSubagentsPanel
                    form={form}
                    isSaving={isSaving}
                    parentPreset={preset}
                    agentPresets={agentPresets}
                    subagentFields={subagentFields}
                    onAddSubagent={onAddSubagent}
                    onOpenSubagent={(index) =>
                      onDrillChange({ kind: "subagent", index })
                    }
                  />
                </TabsContent>
                <TabsContent value="output" className="mt-0">
                  <AgentPresetStructuredOutputPanel
                    form={form}
                    isSaving={isSaving}
                  />
                </TabsContent>
              </>
            )}
          </div>
        </ScrollArea>
      </Tabs>
    </aside>
  )
}

/** Model, thinking, internet, retries, and output as one property list. */
function AgentPresetSettingsRows({
  form,
  isSaving,
  enabledModelOptions,
  enabledModelsLoaded,
  hasStdioMcp,
  onOpenOutput,
}: {
  form: UseFormReturn<AgentPresetFormValues>
  isSaving: boolean
  enabledModelOptions: EnabledModelOption[]
  enabledModelsLoaded: boolean
  hasStdioMcp: boolean
  onOpenOutput: () => void
}) {
  const catalogId = useWatch({ control: form.control, name: "catalog_id" })
  const sourceId = useWatch({ control: form.control, name: "source_id" })
  const modelProvider = useWatch({
    control: form.control,
    name: "model_provider",
  })
  const modelName = useWatch({ control: form.control, name: "model_name" })
  const baseUrl = useWatch({ control: form.control, name: "base_url" })
  const thinking = useWatch({ control: form.control, name: "enableThinking" })
  const internet = useWatch({
    control: form.control,
    name: "enableInternetAccess",
  })
  const retries = Number(useWatch({ control: form.control, name: "retries" }))
  const outputKind = useWatch({ control: form.control, name: "outputTypeKind" })
  const outputDataType = useWatch({
    control: form.control,
    name: "outputTypeDataType",
  })
  const outputJson = useWatch({ control: form.control, name: "outputTypeJson" })
  const retriesError = form.formState.errors.retries?.message
  const output = describeOutput(outputKind, outputDataType, outputJson)
  const modelLabelId = useId()
  const changeOutputKind = useOutputKindChange(form, outputDataType)
  const { type: outputType, isList: outputIsList } =
    parseAgentOutputType(outputDataType)

  function setStructuredType(primitive: AgentOutputPrimitive) {
    if (outputKind !== "data-type") changeOutputKind("data-type")
    form.setValue(
      "outputTypeDataType",
      formatAgentOutputType({
        type: primitive,
        isList: outputKind === "data-type" && outputIsList,
      }),
      { shouldDirty: true, shouldValidate: true }
    )
  }

  function setRetries(next: number) {
    form.setValue("retries", Math.max(0, next), {
      shouldDirty: true,
      shouldValidate: true,
    })
  }

  return (
    <div>
      <div className={RAIL_ROW}>
        <span id={modelLabelId} className="text-muted-foreground">
          Model
        </span>
        <AgentModelCombobox
          options={enabledModelOptions}
          value={{ catalogId, sourceId, modelProvider, modelName, baseUrl }}
          onChange={(option) => {
            form.setValue("model_name", option.modelName, {
              shouldDirty: true,
            })
            syncFormModelSelection(form, option, true)
          }}
          loaded={enabledModelsLoaded}
          disabled={isSaving || enabledModelOptions.length === 0}
          placeholder={
            enabledModelOptions.length ? "Select a model" : "No enabled models"
          }
          aria-labelledby={modelLabelId}
          inline
          triggerClassName="h-8 w-full justify-start rounded-md border-0 bg-transparent px-0 text-[13px] font-normal shadow-none hover:bg-transparent focus-visible:ring-1 focus-visible:ring-inset focus-visible:ring-ring"
        />
      </div>
      <button
        type="button"
        className={RAIL_ROW}
        aria-pressed={thinking}
        disabled={isSaving}
        onClick={() =>
          form.setValue("enableThinking", !thinking, { shouldDirty: true })
        }
      >
        <span className="text-muted-foreground">Thinking</span>
        <span
          className={thinking ? "text-foreground" : "text-muted-foreground"}
        >
          {thinking ? "On" : "Off"}
        </span>
      </button>
      {hasStdioMcp ? (
        <Tooltip>
          <TooltipTrigger asChild>
            <button
              type="button"
              className={cn(RAIL_ROW, "cursor-default")}
              aria-pressed
              aria-disabled
            >
              <span className="text-muted-foreground">Internet</span>
              <span>On</span>
            </button>
          </TooltipTrigger>
          <TooltipContent>
            Internet access is required when a stdio MCP server is connected
          </TooltipContent>
        </Tooltip>
      ) : (
        <button
          type="button"
          className={RAIL_ROW}
          aria-pressed={internet}
          disabled={isSaving}
          onClick={() =>
            form.setValue("enableInternetAccess", !internet, {
              shouldDirty: true,
            })
          }
        >
          <span className="text-muted-foreground">Internet</span>
          <span
            className={internet ? "text-foreground" : "text-muted-foreground"}
          >
            {internet ? "On" : "Off"}
          </span>
        </button>
      )}
      <div className={cn(RAIL_ROW, "group")}>
        <span className="text-muted-foreground">Retries</span>
        <span className="flex items-center gap-1.5">
          <span className="tabular-nums">
            {Number.isFinite(retries) ? retries : DEFAULT_RETRIES}
          </span>
          <span className="flex gap-0.5 opacity-0 transition-opacity group-focus-within:opacity-100 group-hover:opacity-100">
            <Button
              type="button"
              variant="ghost"
              size="icon"
              className="size-5 text-muted-foreground"
              aria-label="Fewer retries"
              disabled={isSaving || retries <= 0}
              onClick={() => setRetries(retries - 1)}
            >
              <Minus className="size-3" />
            </Button>
            <Button
              type="button"
              variant="ghost"
              size="icon"
              className="size-5 text-muted-foreground"
              aria-label="More retries"
              disabled={isSaving}
              onClick={() => setRetries(retries + 1)}
            >
              <Plus className="size-3" />
            </Button>
          </span>
          {retriesError ? (
            <span className="text-destructive">{retriesError}</span>
          ) : null}
        </span>
      </div>
      <DropdownMenu>
        <DropdownMenuTrigger asChild>
          <button type="button" className={RAIL_ROW} disabled={isSaving}>
            <span className="text-muted-foreground">Output</span>
            <span className="flex min-w-0 items-center gap-1">
              <span className="truncate">{output.label}</span>
              {output.meta ? (
                <span
                  className={cn(
                    "shrink-0",
                    output.invalid
                      ? "text-destructive"
                      : "text-muted-foreground"
                  )}
                >
                  · {output.meta}
                </span>
              ) : null}
              <ChevronDown className="ml-0.5 size-3 shrink-0 text-muted-foreground/70" />
            </span>
          </button>
        </DropdownMenuTrigger>
        {/* Item text (after the 32px indicator inset) lines up with the value column. */}
        <DropdownMenuContent
          align="start"
          alignOffset={80}
          className="w-52 text-xs"
        >
          <DropdownMenuRadioGroup value={outputKind}>
            <DropdownMenuRadioItem
              value="none"
              className="text-xs"
              onSelect={() => changeOutputKind("none")}
            >
              Text
            </DropdownMenuRadioItem>
            <DropdownMenuSub>
              <DropdownMenuSubTrigger className="relative pl-8 text-xs">
                {outputKind === "data-type" ? (
                  <span className="absolute left-2 flex size-3.5 items-center justify-center">
                    <DotFilledIcon className="size-4 fill-current" />
                  </span>
                ) : null}
                Structured
                {outputKind === "data-type" && outputDataType ? (
                  <span className="ml-auto pl-2 font-mono text-[11px] text-muted-foreground">
                    {outputDataType}
                  </span>
                ) : null}
              </DropdownMenuSubTrigger>
              <DropdownMenuSubContent className="w-44 text-xs">
                <DropdownMenuRadioGroup
                  value={outputKind === "data-type" ? outputType : ""}
                >
                  {OUTPUT_PRIMITIVES.map((primitive) => (
                    <DropdownMenuRadioItem
                      key={primitive.value}
                      value={primitive.value}
                      className="text-xs"
                      onSelect={() => setStructuredType(primitive.value)}
                    >
                      {primitive.label}
                    </DropdownMenuRadioItem>
                  ))}
                </DropdownMenuRadioGroup>
                <DropdownMenuSeparator />
                <DropdownMenuCheckboxItem
                  className="text-xs"
                  checked={outputKind === "data-type" && outputIsList}
                  disabled={outputKind !== "data-type" || !outputType}
                  onSelect={(event) => event.preventDefault()}
                  onCheckedChange={(checked) =>
                    form.setValue(
                      "outputTypeDataType",
                      formatAgentOutputType({
                        type: outputType as AgentOutputPrimitive,
                        isList: checked === true,
                      }),
                      { shouldDirty: true, shouldValidate: true }
                    )
                  }
                >
                  List of values
                </DropdownMenuCheckboxItem>
              </DropdownMenuSubContent>
            </DropdownMenuSub>
            <DropdownMenuRadioItem
              value="json"
              className="text-xs"
              onSelect={() => {
                if (outputKind !== "json") changeOutputKind("json")
                onOpenOutput()
              }}
            >
              JSON schema
              <span className="ml-auto pl-2 text-muted-foreground">
                {outputKind === "json" ? "Edit" : ""}
              </span>
              <ChevronRight className="size-4" />
            </DropdownMenuRadioItem>
          </DropdownMenuRadioGroup>
        </DropdownMenuContent>
      </DropdownMenu>
    </div>
  )
}

const OUTPUT_PRIMITIVES: Array<{ value: AgentOutputPrimitive; label: string }> =
  [
    { value: "str", label: "String" },
    { value: "int", label: "Integer" },
    { value: "float", label: "Float" },
    { value: "bool", label: "Boolean" },
  ]

function describeOutput(
  kind: AgentPresetFormValues["outputTypeKind"],
  dataType: string | undefined,
  json: string | undefined
): { label: string; meta: string; invalid: boolean } {
  if (kind === "data-type") {
    return { label: "Structured", meta: dataType ?? "", invalid: false }
  }
  if (kind !== "json") {
    return { label: "Text", meta: "", invalid: false }
  }
  try {
    const parsed = JSON.parse(json ?? "")
    const properties =
      parsed && typeof parsed === "object" && !Array.isArray(parsed)
        ? parsed.properties
        : undefined
    if (!properties || typeof properties !== "object") {
      return { label: "JSON schema", meta: "", invalid: false }
    }
    const count = Object.keys(properties).length
    return {
      label: "JSON schema",
      meta: `${count} ${count === 1 ? "field" : "fields"}`,
      invalid: false,
    }
  } catch {
    return { label: "JSON schema", meta: "invalid", invalid: true }
  }
}

/**
 * Tool limit and skill-provided tools for the current draft: saved values while
 * skills and namespace filters are unchanged, a server preview otherwise.
 */
function useLiveToolPolicy({
  workspaceId,
  form,
  savedSkillIds,
  savedNamespaces,
  savedSkillActions,
  maxTools,
}: {
  workspaceId: string
  form: UseFormReturn<AgentPresetFormValues>
  savedSkillIds?: string[]
  savedNamespaces?: string[] | null
  savedSkillActions?: string[] | null
  maxTools?: number | null
}): { skillActions?: string[] | null; maxTools?: number | null } {
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
  return {
    skillActions,
    maxTools:
      maxTools ??
      (savedSkillIds === undefined ? preview?.max_tools : undefined),
  }
}

/** Configure tools, previewing skill-granted tools from the current form. */
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
  mcpIntegrations,
  maxTools,
  savedSkillActions,
  onOpenGroup,
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
  mcpIntegrations: MCPIntegrationRead[]
  maxTools?: number | null
  savedSkillActions?: string[] | null
  onOpenGroup: (groupKey: string, title: string) => void
}) {
  const policy = useLiveToolPolicy({
    workspaceId,
    form,
    savedSkillIds,
    savedNamespaces,
    savedSkillActions,
    maxTools,
  })
  return (
    <AgentPresetRailTools
      registryActions={registryActions}
      registryLoading={registryLoading}
      mcpLoading={mcpLoading}
      toolsLoadError={toolsLoadError}
      mcpIntegrations={mcpIntegrations}
      isSaving={isSaving}
      maxTools={policy.maxTools}
      skillActions={policy.skillActions}
      onOpenGroup={onOpenGroup}
    />
  )
}

/** Attached subagents as compact rows; each opens a detail view to edit it. */
export function AgentPresetSubagentsPanel({
  form,
  isSaving,
  parentPreset,
  agentPresets,
  subagentFields,
  onAddSubagent,
  onOpenSubagent,
}: {
  form: UseFormReturn<AgentPresetFormValues>
  isSaving: boolean
  parentPreset: AgentPresetRead | null
  agentPresets: AgentPresetReadMinimal[]
  subagentFields: Array<{ id: string }>
  onAddSubagent: (subagent: SubagentFormValue) => void
  onOpenSubagent: (index: number) => void
}) {
  const [isPickerOpen, setIsPickerOpen] = useState(false)
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
  const subagentErrors = form.formState.errors.subagents
  const attachedPresetIds = new Set(
    selectedSubagents.map(
      (subagent) =>
        getSubagentPreset({
          subagent,
          presetsById: presetOptionsById,
          presetsBySlug: presetOptionsBySlug,
        })?.id
    )
  )

  function handleAddSubagent(preset: AgentPresetReadMinimal) {
    if (attachedPresetIds.has(preset.id)) {
      return
    }
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
    <section>
      <RailSectionHeader
        title="Subagents"
        count={subagentFields.length}
        addLabel="Add subagent"
        onAdd={() => setIsPickerOpen(true)}
        addDisabled={isSaving || presetOptions.length === 0}
        addDisabledReason={
          presetOptions.length === 0
            ? "Create another agent preset first, then attach it here."
            : null
        }
      />
      {internetAccessWarningMessage ? (
        <Alert variant="warning" className="my-2">
          <AlertCircle className="size-4" />
          <AlertTitle>Internet access limited</AlertTitle>
          <AlertDescription>{internetAccessWarningMessage}</AlertDescription>
        </Alert>
      ) : null}
      {subagentFields.length === 0 ? <RailEmpty /> : null}
      {subagentFields.map((item, index) => {
        const subagent = selectedSubagents[index]
        if (!subagent) {
          return null
        }
        const preset = getSubagentPreset({
          subagent,
          presetsById: presetOptionsById,
          presetsBySlug: presetOptionsBySlug,
        })
        const presetName = preset?.name ?? `${subagent.preset} unavailable`
        const alias = getSubagentFormAlias(subagent)
        const invalid =
          Boolean(subagentErrors?.[index]) ||
          Boolean(preset && getSubagentEligibilityIssue({ preset }))
        return (
          <button
            key={item.id}
            type="button"
            onClick={() => onOpenSubagent(index)}
            aria-label={`${presetName} ${alias}`}
            className={RAIL_LIST_ROW}
          >
            <LogoTile tint="indigo">
              <MousePointerClickIcon />
            </LogoTile>
            <span
              className={cn(
                "truncate",
                !preset && "text-muted-foreground",
                invalid && "text-destructive"
              )}
            >
              {presetName}
            </span>
            {preset?.model_name ? (
              <span className="shrink-0 text-xs text-muted-foreground">
                {preset.model_name}
              </span>
            ) : null}
            <span className="flex-1" />
            {invalid ? (
              <AlertCircle
                className="size-3.5 shrink-0 text-destructive"
                aria-label="Needs attention"
              />
            ) : null}
            <span className="truncate font-mono text-[11.5px] text-muted-foreground/80">
              {alias}
            </span>
          </button>
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
              const attached = attachedPresetIds.has(preset.id)
              return (
                <HoverCard key={preset.id} openDelay={300}>
                  <HoverCardTrigger asChild>
                    <CommandItem
                      value={preset.id}
                      keywords={[preset.name, preset.slug]}
                      disabled={isSaving || attached}
                      aria-description={
                        attached
                          ? "Already added"
                          : (eligibilityMessage ?? undefined)
                      }
                      onSelect={() => handleAddSubagent(preset)}
                      className="flex min-w-0 items-center gap-2"
                    >
                      <MousePointerClickIcon className="!size-4 shrink-0 text-primary" />
                      <span className="min-w-0 truncate">{preset.name}</span>
                      <span className="truncate font-mono text-xs text-muted-foreground">
                        {preset.slug}
                      </span>
                      <span className="ml-auto flex shrink-0 items-center gap-2">
                        {attached ? (
                          <span className="text-xs text-muted-foreground">
                            Added
                          </span>
                        ) : null}
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
    </section>
  )
}

/** Detail view for one attached subagent: alias, delegation rule, turn limit. */
export function AgentPresetSubagentDetail({
  form,
  index,
  isSaving,
  parentPreset,
  agentPresets,
  onRemove,
}: {
  form: UseFormReturn<AgentPresetFormValues>
  index: number
  isSaving: boolean
  parentPreset: AgentPresetRead | null
  agentPresets: AgentPresetReadMinimal[]
  onRemove: (index: number) => void
}) {
  const subagent = useWatch({
    control: form.control,
    name: `subagents.${index}`,
  })
  const presetsById = useMemo(
    () =>
      new Map(
        agentPresets
          .filter((preset) => preset.id !== parentPreset?.id)
          .map((preset) => [preset.id, preset])
      ),
    [agentPresets, parentPreset?.id]
  )
  const presetsBySlug = useMemo(
    () => new Map([...presetsById.values()].map((p) => [p.slug, p])),
    [presetsById]
  )
  if (!subagent) return null
  const preset = getSubagentPreset({ subagent, presetsById, presetsBySlug })
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
  const presetSlug = preset?.slug ?? subagent.preset
  const presetName = preset?.name ?? `${subagent.preset} unavailable`

  return (
    <div className="flex flex-col gap-3.5 px-2">
      <div className="flex min-h-10 min-w-0 items-center gap-2.5 rounded-lg border px-2.5 text-[13px]">
        <LogoTile tint="indigo">
          <MousePointerClickIcon />
        </LogoTile>
        {preset ? (
          <Link
            href={`/workspaces/${preset.workspace_id}/agents/${preset.id}`}
            target="_blank"
            rel="noopener noreferrer"
            className="min-w-0 truncate rounded-sm underline-offset-2 hover:underline focus-visible:outline-none focus-visible:ring-1 focus-visible:ring-inset focus-visible:ring-ring"
          >
            {presetName}
          </Link>
        ) : (
          <span className="min-w-0 truncate text-muted-foreground">
            {presetName}
          </span>
        )}
        <span className="truncate font-mono text-[11.5px] text-muted-foreground">
          {presetSlug}
        </span>
        <span className="flex-1" />
        {preset?.model_name ? (
          <span className="flex shrink-0 items-center gap-1 text-xs text-muted-foreground">
            <ProviderIcon
              providerId={getModelProviderIconId(preset.model_provider)}
              className="size-3 shrink-0 rounded-none bg-transparent p-0"
            />
            {preset.model_name}
          </span>
        ) : null}
      </div>
      {eligibilityIssue ? (
        <Alert variant="destructive" className="text-xs">
          <AlertCircle className="size-4" />
          <AlertTitle>Cannot attach this preset</AlertTitle>
          <AlertDescription>{eligibilityIssue.message}</AlertDescription>
        </Alert>
      ) : null}
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
      <FormField
        control={form.control}
        name={`subagents.${index}.name`}
        render={({ field }) => (
          <FormItem className="space-y-1.5">
            <FormLabel className="text-xs font-normal text-muted-foreground">
              Alias
            </FormLabel>
            <FormControl>
              <Input
                {...field}
                placeholder={presetSlug}
                disabled={isSaving}
                className="h-8 font-mono text-xs shadow-none"
              />
            </FormControl>
            <FormMessage>{presetAliasError}</FormMessage>
          </FormItem>
        )}
      />
      <FormField
        control={form.control}
        name={`subagents.${index}.description`}
        render={({ field }) => (
          <FormItem className="space-y-1.5">
            <FormLabel className="text-xs font-normal text-muted-foreground">
              Delegate when
            </FormLabel>
            <FormControl>
              <Textarea
                {...field}
                placeholder="When should the parent delegate to this subagent?"
                disabled={isSaving}
                rows={3}
                className="text-[13px] shadow-none"
              />
            </FormControl>
            <FormMessage />
          </FormItem>
        )}
      />
      <FormField
        control={form.control}
        name={`subagents.${index}.maxTurns`}
        render={({ field }) => (
          <FormItem className="space-y-1.5">
            <FormLabel className="text-xs font-normal text-muted-foreground">
              Max turns
            </FormLabel>
            <FormControl>
              <Input
                {...field}
                type="number"
                min={1}
                placeholder="No limit"
                disabled={isSaving}
                className="h-8 w-24 text-[13px] shadow-none"
              />
            </FormControl>
            <FormMessage />
          </FormItem>
        )}
      />
      <div className="mt-1">
        <Button
          type="button"
          variant="ghost"
          size="sm"
          className="-ml-2.5 h-[30px] px-2.5 text-[13px] font-normal text-destructive hover:text-destructive"
          onClick={() => onRemove(index)}
          disabled={isSaving}
        >
          Remove subagent
        </Button>
      </div>
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
  const selectedLibrarySkills = form.watch("librarySkills")
  const { skills, skillsLoading, skillsError } = useSkills(workspaceId)
  const { librarySkills, librarySkillsIsLoading, librarySkillsError } =
    useSkillLibrary(workspaceId)
  const librarySkillsBySlug = useMemo(
    () => new Map((librarySkills ?? []).map((skill) => [skill.slug, skill])),
    [librarySkills]
  )
  const availableLibrarySkillsToAdd = useMemo(
    () =>
      (librarySkills ?? [])
        .filter(
          (skill) =>
            skill.installed &&
            !(selectedLibrarySkills ?? []).includes(skill.slug)
        )
        .sort((a, b) => a.slug.localeCompare(b.slug)),
    [librarySkills, selectedLibrarySkills]
  )
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
  if (
    !skillsLoading &&
    !skillsError &&
    availableSkillsToAdd.length === 0 &&
    availableLibrarySkillsToAdd.length === 0
  ) {
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
  function handleAddSkill(skillId: string) {
    onAddSkillBinding({ skillId })
    setIsPickerOpen(false)
  }

  function setLibrarySkills(slugs: string[]) {
    form.setValue("librarySkills", slugs, { shouldDirty: true })
  }

  function handleAddLibrarySkill(slug: string) {
    setLibrarySkills(
      [...new Set([...(selectedLibrarySkills ?? []), slug])].sort((a, b) =>
        a.localeCompare(b)
      )
    )
    setIsPickerOpen(false)
  }

  function handleRemoveLibrarySkill(slug: string) {
    setLibrarySkills(
      (selectedLibrarySkills ?? []).filter((selected) => selected !== slug)
    )
  }

  const attachedLibrarySkills = selectedLibrarySkills ?? []
  const attachedCount = skillFields.length + attachedLibrarySkills.length

  function renderSkills() {
    // Workspace and library requests fail and load independently.
    const alerts = [
      { error: skillsError, title: "Unable to load workspace skills" },
      {
        error: librarySkillsError,
        title: "Unable to load the Tracecat library",
      },
    ].map(({ error, title }) =>
      error ? (
        <Alert key={title} variant="destructive" className="my-2">
          <AlertCircle className="size-4" />
          <AlertTitle>{title}</AlertTitle>
          <AlertDescription>
            {getApiErrorDetail(error) ?? "Please try again."}
          </AlertDescription>
        </Alert>
      ) : null
    )
    if (skillsLoading || librarySkillsIsLoading) {
      return (
        <>
          {alerts}
          <p className="px-2 text-[13px] text-muted-foreground">
            Loading skills...
          </p>
        </>
      )
    }
    if (attachedCount === 0) {
      return (
        <>
          {alerts}
          <RailEmpty />
        </>
      )
    }
    return (
      <>
        {alerts}
        {skillsError
          ? null
          : skillFields.map((item, index) => (
              <AgentPresetSkillBindingRow
                key={item.id}
                form={form}
                workspaceId={workspaceId}
                index={index}
                isSaving={isSaving}
                availableSkills={skills ?? []}
                savedBinding={savedBindings?.find(
                  (binding) =>
                    binding.skill_id === selectedSkills[index]?.skillId
                )}
                onRemove={onRemoveSkillBinding}
              />
            ))}
        {attachedLibrarySkills.map((slug) => (
          <AgentPresetLibrarySkillRow
            key={slug}
            slug={slug}
            skill={librarySkillsBySlug.get(slug)}
            workspaceId={workspaceId}
            isSaving={isSaving}
            onRemove={handleRemoveLibrarySkill}
          />
        ))}
      </>
    )
  }

  return (
    <section>
      <RailSectionHeader
        title="Skills"
        count={attachedCount}
        addLabel="Add skill"
        onAdd={() => setIsPickerOpen(true)}
        addDisabled={
          isSaving ||
          skillsLoading ||
          (availableSkillsToAdd.length === 0 &&
            availableLibrarySkillsToAdd.length === 0)
        }
        addDisabledReason={addSkillDisabledReason}
      />
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
          {availableLibrarySkillsToAdd.length > 0 ? (
            <CommandGroup heading="Tracecat library">
              {availableLibrarySkillsToAdd.map((skill) => (
                <CommandItem
                  key={skill.slug}
                  value={`library ${skill.slug} ${skill.description ?? ""}`}
                  disabled={isSaving}
                  onSelect={() => handleAddLibrarySkill(skill.slug)}
                >
                  <div className="flex min-w-0 flex-col gap-0.5">
                    <span>{skill.slug}</span>
                    <span className="truncate text-xs text-muted-foreground">
                      {skill.description?.trim() || skill.slug}
                    </span>
                  </div>
                </CommandItem>
              ))}
            </CommandGroup>
          ) : null}
        </CommandList>
      </CommandDialog>
    </section>
  )
}

function AgentPresetLibrarySkillRow({
  slug,
  skill,
  workspaceId,
  isSaving,
  onRemove,
}: {
  slug: string
  skill?: LibrarySkillRead
  workspaceId: string
  isSaving: boolean
  onRemove: (slug: string) => void
}) {
  const source = skill?.source ?? null
  const group = source?.group
  const providerSlug = libraryProviderSlug(
    group ?? source?.provider ?? "Tracecat"
  )
  const description = skill?.description?.trim()
  return (
    <div className={RAIL_LIST_ROW}>
      <SkillLibraryProviderIcon
        providerSlug={providerSlug}
        className="size-5 shrink-0 p-0.5"
      />
      <div className="flex min-w-0 flex-1 items-center gap-2">
        <HoverCard>
          <HoverCardTrigger asChild>
            <Link
              href={librarySkillPath(workspaceId, { slug, source })}
              className="max-w-full truncate rounded-sm font-mono text-[12.5px] underline-offset-2 hover:underline focus-visible:outline-none focus-visible:ring-1 focus-visible:ring-inset focus-visible:ring-ring"
              target="_blank"
              rel="noopener noreferrer"
            >
              {slug}
            </Link>
          </HoverCardTrigger>
          {description ? (
            <HoverCardContent
              align="start"
              className="w-96 max-w-[calc(100vw-2rem)] space-y-1 p-3 text-xs shadow-none"
            >
              <p className="font-medium">{slug}</p>
              <p className="text-muted-foreground">{description}</p>
            </HoverCardContent>
          ) : null}
        </HoverCard>
        <span className="shrink-0 truncate text-xs text-muted-foreground">
          {group ?? source?.provider ?? "Tracecat library"}
        </span>
      </div>
      <Button
        type="button"
        variant="ghost"
        size="icon"
        className={RAIL_REMOVE_BUTTON}
        onClick={() => onRemove(slug)}
        disabled={isSaving}
        aria-label={`Remove ${slug}`}
      >
        <X className="size-3" />
      </Button>
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
    <div className={RAIL_LIST_ROW}>
      <LogoTile tint="indigo">
        <Pyramid />
      </LogoTile>
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
        className={RAIL_REMOVE_BUTTON}
        onClick={() => onRemove(index)}
        disabled={isSaving}
        aria-label={`Remove ${displaySkillName}`}
      >
        <X className="size-3" />
      </Button>
    </div>
  )
}

/**
 * Switch output format, remembering the structured type across format changes
 * and leaving the form clean when a switch is undone.
 */
function useOutputKindChange(
  form: UseFormReturn<AgentPresetFormValues>,
  dataType: string | undefined
) {
  const rememberedDataType = useRef(dataType)
  return (value: AgentPresetFormValues["outputTypeKind"]) => {
    if (value === "data-type" && !form.getValues("outputTypeDataType")) {
      form.setValue("outputTypeDataType", rememberedDataType.current || "str", {
        shouldDirty: true,
        shouldValidate: true,
      })
    }
    if (
      value !== "data-type" &&
      form.formState.defaultValues?.outputTypeKind !== "data-type"
    ) {
      rememberedDataType.current =
        form.getValues("outputTypeDataType") || rememberedDataType.current
      // setValue, not resetField: resetField skips unregistered fields.
      form.setValue(
        "outputTypeDataType",
        form.formState.defaultValues?.outputTypeDataType ?? "",
        { shouldDirty: true }
      )
    }
    form.setValue("outputTypeKind", value, { shouldDirty: true })
  }
}

/** Configure text, primitive, or JSON-schema output without changing its stored shape. */
export function AgentPresetStructuredOutputPanel({
  form,
  isSaving,
}: {
  form: UseFormReturn<AgentPresetFormValues>
  isSaving: boolean
}) {
  const outputTypeKind = useWatch({
    control: form.control,
    name: "outputTypeKind",
  })
  const outputTypeDataType = useWatch({
    control: form.control,
    name: "outputTypeDataType",
  })
  const outputTypeJson = useWatch({
    control: form.control,
    name: "outputTypeJson",
  })
  const { type, isList } = parseAgentOutputType(outputTypeDataType)
  const schema = describeJsonSchema(outputTypeJson)
  const changeKind = useOutputKindChange(form, outputTypeDataType)

  function formatSchema() {
    if (!schema.ok || !outputTypeJson) return
    form.setValue(
      "outputTypeJson",
      JSON.stringify(JSON.parse(outputTypeJson), null, 2),
      { shouldDirty: true, shouldValidate: true }
    )
  }

  return (
    <div className="px-2 text-xs">
      <div className="mb-3.5">
        <SegmentedControl<AgentPresetFormValues["outputTypeKind"]>
          ariaLabel="Format"
          value={outputTypeKind}
          onValueChange={changeKind}
          disabled={isSaving}
          options={[
            { value: "none", label: "Text" },
            { value: "data-type", label: "Structured" },
            { value: "json", label: "JSON schema" },
          ]}
        />
      </div>
      {outputTypeKind === "none" ? (
        <p className="text-[13px] leading-normal text-muted-foreground">
          The agent replies in free-form text. Switch to JSON schema to get
          typed fields a workflow can branch on.
        </p>
      ) : null}
      {outputTypeKind === "data-type" ? (
        <div className="flex flex-col gap-3">
          <FormField
            control={form.control}
            name="outputTypeDataType"
            render={({ field }) => (
              <FormItem className="space-y-1.5">
                <FormLabel className="text-xs font-normal text-muted-foreground">
                  Type
                </FormLabel>
                <FormControl>
                  <SegmentedControl<AgentOutputPrimitive>
                    ariaLabel="Type"
                    value={type as AgentOutputPrimitive}
                    onValueChange={(value) =>
                      field.onChange(
                        formatAgentOutputType({ type: value, isList })
                      )
                    }
                    disabled={isSaving}
                    options={[
                      { value: "str", label: "String" },
                      { value: "int", label: "Integer" },
                      { value: "float", label: "Float" },
                      { value: "bool", label: "Boolean" },
                    ]}
                  />
                </FormControl>
                <FormMessage />
              </FormItem>
            )}
          />
          <FormField
            control={form.control}
            name="outputTypeDataType"
            render={({ field }) => (
              <FormItem className="flex items-center gap-3 space-y-0">
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
                    disabled={isSaving || !type}
                    className="shadow-none focus-visible:ring-1 focus-visible:ring-inset focus-visible:ring-ring [&_span]:shadow-none"
                  />
                </FormControl>
                <FormLabel className="text-[13px] font-normal">
                  List of values
                </FormLabel>
              </FormItem>
            )}
          />
          <p className="text-muted-foreground">
            Returns <output className="font-mono">{outputTypeDataType}</output>
          </p>
        </div>
      ) : null}
      {outputTypeKind === "json" ? (
        <FormField
          control={form.control}
          name="outputTypeJson"
          render={({ field }) => (
            <FormItem className="space-y-0">
              <FormLabel className="sr-only">JSON schema</FormLabel>
              <FormControl>
                <CodeEditor
                  value={field.value ?? ""}
                  onChange={field.onChange}
                  language="json"
                  readOnly={isSaving}
                  className={cn(
                    "min-h-[320px] overflow-hidden rounded-lg border",
                    !schema.ok && field.value && "border-destructive/50"
                  )}
                />
              </FormControl>
              <div className="mt-1.5 flex items-center gap-2">
                <span
                  className={cn(
                    "min-w-0 flex-1 truncate",
                    schema.ok ? "text-muted-foreground" : "text-destructive"
                  )}
                >
                  {schema.status}
                </span>
                <Button
                  type="button"
                  variant="ghost"
                  size="sm"
                  className="h-6 px-2 text-xs font-normal text-muted-foreground"
                  onClick={formatSchema}
                  disabled={isSaving || !schema.ok}
                >
                  Format
                </Button>
              </div>
              <FormMessage />
              {schema.ok && schema.fields.length > 0 ? (
                <div className="mt-4">
                  <div className="pb-1 text-muted-foreground">
                    Fields the agent returns
                  </div>
                  {schema.fields.map((schemaField) => (
                    <div
                      key={schemaField.name}
                      className="flex min-h-[34px] items-center gap-2.5 border-t border-border/60"
                    >
                      <span className="min-w-24 font-mono text-[12.5px]">
                        {schemaField.name}
                      </span>
                      <span className="min-w-[52px] text-[12.5px] text-muted-foreground">
                        {schemaField.type}
                      </span>
                      <span className="min-w-0 flex-1 truncate text-[12.5px] text-muted-foreground">
                        {schemaField.options}
                      </span>
                      <span className="text-[11.5px] text-muted-foreground/70">
                        {schemaField.required ? "required" : ""}
                      </span>
                    </div>
                  ))}
                </div>
              ) : null}
            </FormItem>
          )}
        />
      ) : null}
    </div>
  )
}

type JsonSchemaSummary = {
  ok: boolean
  status: string
  fields: Array<{
    name: string
    type: string
    options: string
    required: boolean
  }>
}

/** Summarise an object schema's top-level properties for display. */
function describeJsonSchema(text: string | undefined): JsonSchemaSummary {
  if (!text?.trim()) {
    return { ok: false, status: "Paste a JSON schema", fields: [] }
  }
  let parsed: unknown
  try {
    parsed = JSON.parse(text)
  } catch (error) {
    const message = error instanceof Error ? error.message : "parse error"
    return { ok: false, status: `Invalid JSON: ${message}`, fields: [] }
  }
  if (!parsed || typeof parsed !== "object" || Array.isArray(parsed)) {
    return { ok: false, status: "JSON schema must be an object", fields: [] }
  }
  const schema = parsed as {
    properties?: Record<string, { type?: unknown; enum?: unknown }>
    required?: unknown
  }
  const properties =
    schema.properties && typeof schema.properties === "object"
      ? schema.properties
      : {}
  const required = Array.isArray(schema.required) ? schema.required : []
  const fields = Object.entries(properties).map(([name, property]) => {
    const values = Array.isArray(property?.enum) ? property.enum : null
    let type = typeof property?.type === "string" ? property.type : "any"
    if (values) type = "one of"
    else if (type === "string") type = "text"
    return {
      name,
      type,
      options: values ? values.map(String).join(" · ") : "",
      required: required.includes(name),
    }
  })
  const count = `${fields.length} ${fields.length === 1 ? "field" : "fields"}`
  return { ok: true, status: `Valid · ${count}`, fields }
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
    librarySkills: preset.library_skills ?? [],
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
    library_skills:
      values.librarySkills.length > 0 ? values.librarySkills : null,
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
