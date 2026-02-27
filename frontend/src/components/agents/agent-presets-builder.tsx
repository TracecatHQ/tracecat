"use client"

import { zodResolver } from "@hookform/resolvers/zod"
import {
  AlertCircle,
  Box,
  Braces,
  Brackets,
  Hash,
  List,
  ListOrdered,
  ListTodo,
  Loader2,
  MessageCircle,
  MoreVertical,
  Percent,
  Plus,
  Save,
  SlidersHorizontal,
  Sparkles,
  ToggleLeft,
  Trash2,
  Type,
} from "lucide-react"
import Link from "next/link"
import { useRouter } from "next/navigation"
import {
  type MouseEvent,
  useCallback,
  useEffect,
  useMemo,
  useRef,
  useState,
} from "react"
import { type UseFormReturn, useFieldArray, useForm } from "react-hook-form"
import { z } from "zod"
import type {
  AgentPresetCreate,
  AgentPresetRead,
  AgentPresetUpdate,
} from "@/client"
import { ActionSelect } from "@/components/chat/action-select"
import { ChatHistoryDropdown } from "@/components/chat/chat-history-dropdown"
import { ChatSessionPane } from "@/components/chat/chat-session-pane"
import { CodeEditor } from "@/components/editor/codemirror/code-editor"
import { getIcon, ProviderIcon } from "@/components/icons"
import { CenteredSpinner } from "@/components/loading/spinner"
import { MultiTagCommandInput, type Suggestion } from "@/components/tags-input"
import { SimpleEditor } from "@/components/tiptap-templates/simple/simple-editor"
import { Alert, AlertDescription, AlertTitle } from "@/components/ui/alert"
import {
  AlertDialog,
  AlertDialogAction,
  AlertDialogCancel,
  AlertDialogContent,
  AlertDialogDescription,
  AlertDialogFooter,
  AlertDialogHeader,
  AlertDialogTitle,
  AlertDialogTrigger,
} from "@/components/ui/alert-dialog"
import { Button } from "@/components/ui/button"
import {
  DropdownMenu,
  DropdownMenuContent,
  DropdownMenuItem,
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
} from "@/components/ui/form"
import { Input } from "@/components/ui/input"
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
  useAgentPreset,
  useAgentPresets,
  useCreateAgentPreset,
  useDeleteAgentPreset,
  useUpdateAgentPreset,
} from "@/hooks"
import { useCreateChat, useGetChatVercel, useListChats } from "@/hooks/use-chat"
import { useEntitlements } from "@/hooks/use-entitlements"
import type { ModelInfo } from "@/lib/chat"
import {
  useAgentModels,
  useChatReadiness,
  useListMcpIntegrations,
  useModelProviders,
  useRegistryActions,
  useWorkspaceModelProvidersStatus,
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
  }

  const normalized = slug.toLowerCase()
  if (slugMap[normalized]) {
    return slugMap[normalized]
  }

  if (normalized.endsWith("_mcp")) {
    return normalized
  }

  return undefined
}

const agentPresetSchema = z
  .object({
    name: z.string().min(1, "Name is required"),
    slug: z.string().min(1, "Slug is required"),
    description: z.string().max(1000).optional(),
    instructions: z.string().optional(),
    model_provider: z.string().min(1, "Model provider is required"),
    model_name: z.string().min(1, "Model name is required"),
    base_url: z.union([z.string().url(), z.literal(""), z.undefined()]),
    outputTypeKind: z.enum(["none", "data-type", "json"]),
    outputTypeDataType: z.string().optional(),
    outputTypeJson: z.string().optional(),
    actions: z.array(z.string()).default([]),
    namespaces: z.array(z.string()).default([]),
    mcpIntegrations: z.array(z.string()).default([]),
    toolApprovals: z
      .array(
        z.object({
          tool: z.string().min(1, "Tool name is required"),
          allow: z.boolean(),
        })
      )
      .default([]),
    retries: z.coerce
      .number({ invalid_type_error: "Retries must be a number" })
      .int()
      .min(0, "Retries must be 0 or more"),
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
  })

type AgentPresetFormValues = z.infer<typeof agentPresetSchema>
type ToolApprovalFormValue = AgentPresetFormValues["toolApprovals"][number]

const DEFAULT_FORM_VALUES: AgentPresetFormValues = {
  name: "",
  slug: "",
  description: "",
  instructions: "",
  model_provider: "",
  model_name: "",
  base_url: "",
  outputTypeKind: "none",
  outputTypeDataType: "",
  outputTypeJson: "",
  actions: [],
  namespaces: [],
  mcpIntegrations: [],
  toolApprovals: [],
  retries: DEFAULT_RETRIES,
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
  const { providers } = useModelProviders()
  const { models } = useAgentModels()

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

  const modelOptionsByProvider = useMemo(() => {
    if (!models) {
      return {}
    }
    const grouped: Record<string, { label: string; value: string }[]> = {}
    for (const [key, config] of Object.entries(models)) {
      const provider = config.provider
      if (!grouped[provider]) {
        grouped[provider] = []
      }
      grouped[provider]?.push({
        label: config.name ?? key,
        value: config.name ?? key,
      })
    }
    for (const list of Object.values(grouped)) {
      list.sort((a, b) => a.label.localeCompare(b.label))
    }
    return grouped
  }, [models])

  const modelProviderOptions = useMemo(() => {
    const set = new Set<string>()
    providers?.forEach((provider) => set.add(provider))
    Object.keys(modelOptionsByProvider).forEach((provider) => set.add(provider))
    return Array.from(set).sort((a, b) => a.localeCompare(b))
  }, [modelOptionsByProvider, providers])

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
        actionSuggestions={actionSuggestions}
        namespaceSuggestions={namespaceSuggestions}
        modelOptionsByProvider={modelOptionsByProvider}
        modelProviderOptions={modelProviderOptions}
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
}: {
  preset: AgentPresetRead | null
  workspaceId: string
}) {
  const [selectedChatId, setSelectedChatId] = useState<string | null>(null)

  const { providersStatus, isLoading: providersStatusLoading } =
    useWorkspaceModelProvidersStatus(workspaceId)

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
  const { chat, chatLoading, chatError } = useGetChatVercel({
    chatId: activeChatId,
    workspaceId,
  })

  const modelInfo: ModelInfo | null = useMemo(() => {
    if (!preset) {
      return null
    }
    return {
      name: preset.model_name,
      provider: preset.model_provider,
      baseUrl: preset.base_url ?? null,
    }
  }, [preset])

  const providerReady = useMemo(() => {
    if (!preset) {
      return false
    }
    return providersStatus?.[preset.model_provider] ?? false
  }, [providersStatus, preset])

  const canStartChat = Boolean(preset && providerReady)
  const shouldAutoCreateChat =
    canStartChat && !activeChatId && !chatsLoading && !createChatPending

  const handleCreateChat = async () => {
    if (!preset || createChatPending || !providerReady) {
      return
    }

    try {
      const newChat = await createChat({
        title: `${preset.name} chat`,
        entity_type: "agent_preset",
        entity_id: preset.id,
        tools: preset.actions ?? undefined,
      })
      setSelectedChatId(newChat.id)
      await refetchChats()
    } catch (error) {
      console.error("Failed to create agent preset chat", error)
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

    if (providersStatusLoading) {
      return (
        <div className="flex h-full items-center justify-center">
          <CenteredSpinner />
        </div>
      )
    }

    if (!providerReady) {
      return (
        <div className="flex h-full flex-col items-center justify-center px-4">
          <div className="flex max-w-xs flex-col items-center gap-2 text-center text-xs text-muted-foreground">
            <AlertCircle className="size-5 text-amber-500" />
            <p className="text-pretty">
              This agent uses workspace credentials for{" "}
              <span className="font-medium">{preset.model_provider}</span>.
              Configure them on the{" "}
              <Link
                href={`/workspaces/${workspaceId}/credentials`}
                className="font-medium text-primary hover:underline"
                target="_blank"
                rel="noopener noreferrer"
              >
                credentials page
              </Link>{" "}
              to enable chat.
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
        <div className="flex h-10 items-center justify-end border-b px-3">
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

type AgentPresetFormMode = "create" | "edit"
type AgentPresetSideTab =
  | "live-chat"
  | "assistant"
  | "configuration"
  | "structured-output"

type McpIntegrationOption = {
  id: string
  name: string
  description?: string | null
  providerId?: string
}

type AgentPresetFormProps = {
  preset: AgentPresetRead | null
  mode: AgentPresetFormMode
  workspaceId: string
  builderPrompt?: string
  onCreate: (payload: AgentPresetCreate) => Promise<AgentPresetRead>
  onUpdate: (
    presetId: string,
    payload: AgentPresetUpdate
  ) => Promise<AgentPresetRead>
  onDelete?: () => Promise<void>
  isSaving: boolean
  isDeleting: boolean
  actionSuggestions: Suggestion[]
  namespaceSuggestions: Suggestion[]
  modelProviderOptions: string[]
  modelOptionsByProvider: Record<string, { label: string; value: string }[]>
  mcpIntegrations: McpIntegrationOption[]
  mcpIntegrationsIsLoading: boolean
}

function AgentPresetForm({
  preset,
  mode,
  workspaceId,
  builderPrompt,
  onCreate,
  onUpdate,
  onDelete,
  isSaving,
  isDeleting,
  actionSuggestions,
  namespaceSuggestions,
  modelProviderOptions,
  modelOptionsByProvider,
  mcpIntegrations,
  mcpIntegrationsIsLoading,
}: AgentPresetFormProps) {
  const [deleteDialogOpen, setDeleteDialogOpen] = useState(false)
  const [activeTab, setActiveTab] = useState<AgentPresetSideTab>("live-chat")

  const form = useForm<AgentPresetFormValues>({
    resolver: zodResolver(agentPresetSchema),
    mode: "onBlur",
    defaultValues: preset ? presetToFormValues(preset) : DEFAULT_FORM_VALUES,
  })

  const handleConfirmDelete = async (event: MouseEvent<HTMLButtonElement>) => {
    event.preventDefault()
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
    fields: toolApprovalFields,
    append: appendToolApproval,
    remove: removeToolApproval,
  } = useFieldArray({
    control: form.control,
    name: "toolApprovals",
  })

  useEffect(() => {
    const defaults = preset ? presetToFormValues(preset) : DEFAULT_FORM_VALUES
    form.reset(defaults, { keepDirty: false })
  }, [form, mode, preset])

  const watchedName = form.watch("name")
  const providerValue = form.watch("model_provider")
  const modelOptions = modelOptionsByProvider[providerValue] ?? []

  useEffect(() => {
    const nextSlug = slugify(watchedName ?? "", "-")
    if (form.getValues("slug") !== nextSlug) {
      form.setValue("slug", nextSlug, { shouldDirty: false })
    }
  }, [form, watchedName])

  useEffect(() => {
    if (
      modelOptions.length > 0 &&
      !modelOptions.some(
        (option) => option.value === form.getValues("model_name")
      )
    ) {
      form.setValue("model_name", modelOptions[0]?.value ?? "", {
        shouldDirty: false,
      })
    }
  }, [form, modelOptions])

  const handleSubmit = form.handleSubmit(async (values) => {
    const payload = formValuesToPayload(values)
    if (mode === "edit" && preset) {
      const updated = await onUpdate(preset.id, payload)
      form.reset(presetToFormValues(updated))
    } else {
      const created = await onCreate(payload)
      form.reset(presetToFormValues(created))
    }
  })

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

  const handleAddToolApproval = useCallback(() => {
    appendToolApproval({
      tool: "",
      allow: true,
    })
  }, [appendToolApproval])

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
              onDelete={onDelete}
              deleteDialogOpen={deleteDialogOpen}
              onDeleteDialogChange={handleDeleteDialogChange}
              onConfirmDelete={handleConfirmDelete}
            />
          </ResizablePanel>

          <ResizableHandle withHandle />

          <ResizablePanel defaultSize={38} minSize={26}>
            <AgentPresetRightPanel
              activeTab={activeTab}
              onTabChange={setActiveTab}
              preset={preset}
              workspaceId={workspaceId}
              builderPrompt={builderPrompt}
              form={form}
              isSaving={isSaving}
              actionSuggestions={actionSuggestions}
              namespaceSuggestions={namespaceSuggestions}
              modelProviderOptions={modelProviderOptions}
              modelOptionsByProvider={modelOptionsByProvider}
              mcpIntegrations={mcpIntegrations}
              mcpIntegrationsIsLoading={mcpIntegrationsIsLoading}
              toolApprovalFields={toolApprovalFields}
              onAddToolApproval={handleAddToolApproval}
              onRemoveToolApproval={removeToolApproval}
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
  onDelete?: () => Promise<void>
  deleteDialogOpen: boolean
  onDeleteDialogChange: (nextOpen: boolean) => void
  onConfirmDelete: (event: MouseEvent<HTMLButtonElement>) => Promise<void>
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
              <AlertDialog
                open={deleteDialogOpen}
                onOpenChange={onDeleteDialogChange}
              >
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
                    <AlertDialogTrigger asChild>
                      <DropdownMenuItem
                        className="text-destructive focus:text-destructive"
                        onSelect={(e) => {
                          e.preventDefault()
                          onDeleteDialogChange(true)
                        }}
                      >
                        <Trash2 className="mr-2 size-4" />
                        Delete agent
                      </DropdownMenuItem>
                    </AlertDialogTrigger>
                  </DropdownMenuContent>
                </DropdownMenu>
                <AlertDialogContent>
                  <AlertDialogHeader>
                    <AlertDialogTitle>
                      Delete this agent preset?
                    </AlertDialogTitle>
                    <AlertDialogDescription>
                      This action permanently removes the preset and cannot be
                      undone.
                    </AlertDialogDescription>
                  </AlertDialogHeader>
                  <AlertDialogFooter>
                    <AlertDialogCancel disabled={isDeleting}>
                      Cancel
                    </AlertDialogCancel>
                    <AlertDialogAction
                      variant="destructive"
                      onClick={onConfirmDelete}
                      disabled={isDeleting}
                    >
                      {isDeleting ? (
                        <span className="flex items-center">
                          <Loader2 className="mr-2 size-4 animate-spin" />
                          Deleting...
                        </span>
                      ) : (
                        "Delete agent"
                      )}
                    </AlertDialogAction>
                  </AlertDialogFooter>
                </AlertDialogContent>
              </AlertDialog>
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
  preset,
  workspaceId,
  builderPrompt,
  form,
  isSaving,
  actionSuggestions,
  namespaceSuggestions,
  modelProviderOptions,
  modelOptionsByProvider,
  mcpIntegrations,
  mcpIntegrationsIsLoading,
  toolApprovalFields,
  onAddToolApproval,
  onRemoveToolApproval,
}: {
  activeTab: AgentPresetSideTab
  onTabChange: (tab: AgentPresetSideTab) => void
  preset: AgentPresetRead | null
  workspaceId: string
  builderPrompt?: string
  form: UseFormReturn<AgentPresetFormValues>
  isSaving: boolean
  actionSuggestions: Suggestion[]
  namespaceSuggestions: Suggestion[]
  modelProviderOptions: string[]
  modelOptionsByProvider: Record<string, { label: string; value: string }[]>
  mcpIntegrations: McpIntegrationOption[]
  mcpIntegrationsIsLoading: boolean
  toolApprovalFields: Array<{ id: string }>
  onAddToolApproval: () => void
  onRemoveToolApproval: (index: number) => void
}) {
  return (
    <div className="flex h-full flex-col overflow-hidden">
      <Tabs
        value={activeTab}
        onValueChange={(value) => onTabChange(value as AgentPresetSideTab)}
        className="flex h-full w-full flex-col"
      >
        <div className="w-full shrink-0">
          <div className="flex items-center justify-start">
            <TabsList className="h-9 justify-start rounded-none bg-transparent p-0">
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
                value="structured-output"
              >
                <Box className="mr-2 size-4" />
                <span>Output</span>
              </TabsTrigger>
            </TabsList>
          </div>
          <Separator />
        </div>

        <div className="flex-1 overflow-hidden">
          <TabsContent value="live-chat" className="mt-0 h-full">
            <AgentPresetChatPane preset={preset} workspaceId={workspaceId} />
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
              modelProviderOptions={modelProviderOptions}
              modelOptionsByProvider={modelOptionsByProvider}
              mcpIntegrations={mcpIntegrations}
              mcpIntegrationsIsLoading={mcpIntegrationsIsLoading}
              toolApprovalFields={toolApprovalFields}
              onAddToolApproval={onAddToolApproval}
              onRemoveToolApproval={onRemoveToolApproval}
            />
          </TabsContent>

          <TabsContent value="structured-output" className="mt-0 h-full">
            <AgentPresetStructuredOutputPanel form={form} isSaving={isSaving} />
          </TabsContent>
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
  modelProviderOptions,
  modelOptionsByProvider,
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
  modelProviderOptions: string[]
  modelOptionsByProvider: Record<string, { label: string; value: string }[]>
  mcpIntegrations: McpIntegrationOption[]
  mcpIntegrationsIsLoading: boolean
  toolApprovalFields: Array<{ id: string }>
  onAddToolApproval: () => void
  onRemoveToolApproval: (index: number) => void
}) {
  const providerValue = form.watch("model_provider")
  const internetAccessEnabled = form.watch("enableInternetAccess")
  const modelOptions = modelOptionsByProvider[providerValue] ?? []

  return (
    <ScrollArea className="h-full">
      <div className="flex flex-col gap-8 px-6 py-6 pb-20 text-sm">
        <section className="space-y-4">
          <div className="grid gap-4 md:grid-cols-2">
            <FormField
              control={form.control}
              name="model_provider"
              render={({ field }) => (
                <FormItem>
                  <FormLabel>Model provider</FormLabel>
                  <FormControl>
                    {modelProviderOptions.length > 0 ? (
                      <Select
                        value={field.value}
                        onValueChange={field.onChange}
                        disabled={isSaving}
                      >
                        <SelectTrigger>
                          <SelectValue placeholder="Select provider" />
                        </SelectTrigger>
                        <SelectContent>
                          {modelProviderOptions.map((provider) => (
                            <SelectItem key={provider} value={provider}>
                              {provider}
                            </SelectItem>
                          ))}
                        </SelectContent>
                      </Select>
                    ) : (
                      <Input
                        placeholder="openai"
                        {...field}
                        disabled={isSaving}
                      />
                    )}
                  </FormControl>
                </FormItem>
              )}
            />
            <FormField
              control={form.control}
              name="model_name"
              render={({ field }) => (
                <FormItem>
                  <FormLabel>Model name</FormLabel>
                  <FormControl>
                    {modelOptions.length > 0 ? (
                      <Select
                        value={field.value}
                        onValueChange={field.onChange}
                        disabled={isSaving}
                      >
                        <SelectTrigger>
                          <SelectValue placeholder="Select model" />
                        </SelectTrigger>
                        <SelectContent>
                          {modelOptions.map((option) => (
                            <SelectItem
                              key={`${providerValue}-${option.value}`}
                              value={option.value}
                            >
                              {option.label}
                            </SelectItem>
                          ))}
                        </SelectContent>
                      </Select>
                    ) : (
                      <Input
                        placeholder="gpt-4o-mini"
                        {...field}
                        disabled={isSaving}
                      />
                    )}
                  </FormControl>
                </FormItem>
              )}
            />
          </div>
          <div className="grid gap-4 md:grid-cols-2">
            <FormField
              control={form.control}
              name="base_url"
              render={({ field }) => (
                <FormItem>
                  <FormLabel>Custom model base URL</FormLabel>
                  <FormControl>
                    <Input
                      placeholder="https://api.openai.com/v1"
                      value={field.value ?? ""}
                      onChange={field.onChange}
                      disabled={isSaving}
                    />
                  </FormControl>
                </FormItem>
              )}
            />
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
          <div className="flex items-center gap-2">
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
            <label
              htmlFor="enable-internet-access"
              className="text-sm font-medium"
            >
              Enable internet access
            </label>
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
                Save the preset name and model provider to activate the builder
                assistant.
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

  return {
    name: preset.name,
    slug: preset.slug,
    description: preset.description ?? "",
    instructions: preset.instructions ?? "",
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
    retries: preset.retries ?? DEFAULT_RETRIES,
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
    model_name: values.model_name,
    model_provider: values.model_provider,
    base_url: normalizeOptional(values.base_url),
    output_type: outputType ?? null,
    actions: values.actions.length > 0 ? values.actions : null,
    namespaces: values.namespaces.length > 0 ? values.namespaces : null,
    mcp_integrations:
      values.mcpIntegrations.length > 0 ? values.mcpIntegrations : null,
    tool_approvals: toToolApprovalMap(values.toolApprovals),
    retries: values.retries,
    enable_internet_access: values.enableInternetAccess,
  }
}

function normalizeOptional(value: string | null | undefined) {
  if (value == null) {
    return null
  }
  const trimmed = value.trim()
  return trimmed.length > 0 ? trimmed : null
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
