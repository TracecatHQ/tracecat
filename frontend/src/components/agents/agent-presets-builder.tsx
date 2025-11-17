"use client"

import { zodResolver } from "@hookform/resolvers/zod"
import {
  AlertCircle,
  Bot,
  Loader2,
  MessageCircle,
  MoreVertical,
  Plus,
  RotateCcw,
  Save,
  Trash2,
} from "lucide-react"
import Link from "next/link"
import { useRouter } from "next/navigation"
import {
  type MouseEvent,
  useCallback,
  useEffect,
  useId,
  useMemo,
  useRef,
  useState,
} from "react"
import { type Control, useFieldArray, useForm } from "react-hook-form"
import { z } from "zod"
import type {
  AgentPresetCreate,
  AgentPresetRead,
  AgentPresetReadMinimal,
  AgentPresetUpdate,
} from "@/client"
import { ActionSelect } from "@/components/chat/action-select"
import { ChatSessionPane } from "@/components/chat/chat-session-pane"
import { getIcon } from "@/components/icons"
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
import { Badge } from "@/components/ui/badge"
import { Button } from "@/components/ui/button"
import {
  DropdownMenu,
  DropdownMenuContent,
  DropdownMenuItem,
  DropdownMenuTrigger,
} from "@/components/ui/dropdown-menu"
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
import { useFeatureFlag } from "@/hooks/use-feature-flags"
import type { ModelInfo } from "@/lib/chat"
import {
  useAgentModels,
  useChatReadiness,
  useModelProviders,
  useModelProvidersStatus,
  useRegistryActions,
} from "@/lib/hooks"
import { cn, slugify } from "@/lib/utils"
import { useWorkspaceId } from "@/providers/workspace-id"

const PRESET_OUTPUT_TYPES = [
  { label: "Text (str)", value: "str" },
  { label: "Boolean", value: "bool" },
  { label: "Number (int)", value: "int" },
  { label: "Number (float)", value: "float" },
  { label: "List of text", value: "list[str]" },
  { label: "List of numbers", value: "list[int]" },
] as const

const NEW_PRESET_ID = "new"
const DEFAULT_RETRIES = 3

const agentPresetSchema = z
  .object({
    name: z.string().min(1, "Name is required"),
    slug: z.string().min(1, "Slug is required"),
    description: z.string().max(1000).optional(),
    instructions: z.string().optional(),
    model_provider: z.string().min(1, "Model provider is required"),
    model_name: z.string().min(1, "Model name is required"),
    base_url: z.union([z.string().url(), z.literal(""), z.undefined()]),
    outputTypeKind: z.enum(["none", "preset", "json"]),
    outputTypePreset: z.string().optional(),
    outputTypeJson: z.string().optional(),
    actions: z.array(z.string()).default([]),
    namespaces: z.array(z.string()).default([]),
    toolApprovals: z
      .array(
        z.object({
          tool: z.string().min(1, "Tool name is required"),
          allow: z.boolean(),
        })
      )
      .default([]),
    mcpServerUrl: z.union([z.string().url(), z.literal(""), z.undefined()]),
    mcpServerHeaders: z
      .array(
        z.object({
          key: z.string().min(1, "Header key is required"),
          value: z.string().optional(),
        })
      )
      .default([]),
    modelSettings: z
      .array(
        z.object({
          key: z.string().min(1, "Setting key is required"),
          value: z.string().optional(),
        })
      )
      .default([]),
    retries: z.coerce
      .number({ invalid_type_error: "Retries must be a number" })
      .int()
      .min(0, "Retries must be 0 or more"),
  })
  .superRefine((data, ctx) => {
    if (data.outputTypeKind === "preset" && !data.outputTypePreset) {
      ctx.addIssue({
        path: ["outputTypePreset"],
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
type KeyValueFormValue = AgentPresetFormValues["modelSettings"][number]

const DEFAULT_FORM_VALUES: AgentPresetFormValues = {
  name: "",
  slug: "",
  description: "",
  instructions: "",
  model_provider: "",
  model_name: "",
  base_url: "",
  outputTypeKind: "none",
  outputTypePreset: "",
  outputTypeJson: "",
  actions: [],
  namespaces: [],
  toolApprovals: [],
  mcpServerUrl: "",
  mcpServerHeaders: [],
  modelSettings: [],
  retries: DEFAULT_RETRIES,
}

export function AgentPresetsBuilder({ presetId }: { presetId?: string }) {
  const router = useRouter()
  const workspaceId = useWorkspaceId()
  const { isFeatureEnabled, isLoading: featureFlagsLoading } = useFeatureFlag()
  const agentPresetsEnabled = isFeatureEnabled("agent-presets")
  const activePresetId = presetId ?? NEW_PRESET_ID

  const { presets, presetsIsLoading, presetsError } = useAgentPresets(
    workspaceId,
    { enabled: agentPresetsEnabled && !featureFlagsLoading }
  )
  const { registryActions } = useRegistryActions()
  const { providers } = useModelProviders()
  const { models } = useAgentModels()
  const [sidebarTab, setSidebarTab] = useState<"presets" | "chat">("presets")
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
      router.replace(`/workspaces/${workspaceId}/agents/${normalizedId}`)
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
      enabled: agentPresetsEnabled && !featureFlagsLoading,
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

  const chatTabDisabled = !selectedPreset

  useEffect(() => {
    if (chatTabDisabled && sidebarTab === "chat") {
      setSidebarTab("presets")
    }
  }, [chatTabDisabled, sidebarTab])

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
      <ResizablePanelGroup direction="horizontal">
        <ResizablePanel defaultSize={26} minSize={18}>
          <div className="flex h-full flex-col border-r bg-muted/40">
            <Tabs
              value={sidebarTab}
              onValueChange={(value) => {
                if (value === "chat" && chatTabDisabled) {
                  return
                }
                setSidebarTab(value as "presets" | "chat")
              }}
              className="flex h-full flex-col"
            >
              <div className="px-3 pt-3">
                <TabsList className="grid w-full grid-cols-2">
                  <TabsTrigger value="presets" disableUnderline>
                    <Bot className="mr-1.5 h-3.5 w-3.5" />
                    Agents
                  </TabsTrigger>
                  <TabsTrigger
                    value="chat"
                    disabled={chatTabDisabled}
                    disableUnderline
                  >
                    <MessageCircle className="mr-1.5 h-3.5 w-3.5" />
                    Live chat
                  </TabsTrigger>
                </TabsList>
              </div>
              <div className="flex-1 min-h-0">
                <TabsContent
                  value="presets"
                  className="h-full flex-col px-0 py-0 data-[state=active]:flex data-[state=inactive]:hidden"
                >
                  <PresetsSidebar
                    presets={presets ?? []}
                    selectedId={activePresetId}
                    workspaceId={workspaceId}
                    onCreate={() => handleSetSelectedPresetId(NEW_PRESET_ID)}
                  />
                </TabsContent>
                <TabsContent
                  value="chat"
                  className="h-full flex-col px-0 py-0 data-[state=active]:flex data-[state=inactive]:hidden"
                >
                  <AgentPresetChatPane
                    preset={selectedPreset ?? null}
                    workspaceId={workspaceId}
                  />
                </TabsContent>
              </div>
            </Tabs>
          </div>
        </ResizablePanel>
        <ResizableHandle withHandle />
        <ResizablePanel defaultSize={50} minSize={34}>
          <AgentPresetForm
            key={selectedPreset?.id ?? NEW_PRESET_ID}
            preset={selectedPreset ?? null}
            mode={selectedPreset ? "edit" : "create"}
            actionSuggestions={actionSuggestions}
            namespaceSuggestions={namespaceSuggestions}
            modelOptionsByProvider={modelOptionsByProvider}
            modelProviderOptions={modelProviderOptions}
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
        </ResizablePanel>
        <ResizableHandle withHandle />
        <ResizablePanel defaultSize={30} minSize={20}>
          <AgentPresetBuilderChatPane
            preset={selectedPreset ?? null}
            workspaceId={workspaceId}
          />
        </ResizablePanel>
      </ResizablePanelGroup>
    </div>
  )
}

function PresetsSidebar({
  presets,
  selectedId,
  workspaceId,
  onCreate,
}: {
  presets: AgentPresetReadMinimal[]
  selectedId: string
  workspaceId: string
  onCreate: () => void
}) {
  const list = presets

  return (
    <div className="flex h-full flex-col">
      <div className="flex items-center justify-between border-b px-3 py-2">
        <div>
          <p className="text-xs text-muted-foreground">{list.length} agents</p>
        </div>
        <Button size="sm" onClick={onCreate} variant="ghost">
          <Plus className="mr-2 size-4" />
          New
        </Button>
      </div>
      <div className="flex-1 min-h-0">
        <ScrollArea className="h-full">
          <div className="space-y-1 px-2 py-3">
            {list.length === 0 ? (
              <div className="flex h-48 flex-col items-center justify-center gap-2 rounded-md border border-dashed px-3 text-center text-xs text-muted-foreground">
                <Bot className="size-4 opacity-60" />
                <span>No saved presets yet.</span>
                <span>
                  Create one to reuse agents across workflows and chat.
                </span>
              </div>
            ) : (
              list.map((preset) => (
                <SidebarItem
                  key={preset.id}
                  href={`/workspaces/${workspaceId}/agents/${preset.id}`}
                  active={selectedId === preset.id}
                  title={preset.name}
                  description={preset.description ?? preset.slug}
                />
              ))
            )}
          </div>
        </ScrollArea>
      </div>
    </div>
  )
}

function SidebarItem({
  active,
  href,
  title,
  description,
  status,
}: {
  active?: boolean
  href: string
  title: string
  description?: string | null
  status?: string | null
}) {
  return (
    <Link
      href={href}
      className={cn(
        "block w-full rounded-md border px-3 py-2.5 text-left transition-colors",
        active
          ? "border-primary bg-primary/10 text-foreground shadow-sm"
          : "border-transparent bg-background text-foreground hover:border-border hover:bg-accent/50"
      )}
    >
      <div className="flex items-center justify-between text-sm font-medium">
        <span className="truncate">{title}</span>
        {status ? (
          <Badge
            variant="outline"
            className={cn(
              "ml-2 border-muted-foreground/40 text-xs font-normal shrink-0",
              active ? "text-foreground/80" : "text-muted-foreground"
            )}
          >
            {status}
          </Badge>
        ) : null}
      </div>
      {description ? (
        <p
          className={cn(
            "mt-1.5 line-clamp-1 text-xs",
            active ? "text-muted-foreground" : "text-muted-foreground/70"
          )}
        >
          {description}
        </p>
      ) : null}
    </Link>
  )
}

function AgentPresetChatPane({
  preset,
  workspaceId,
}: {
  preset: AgentPresetRead | null
  workspaceId: string
}) {
  const [createdChatId, setCreatedChatId] = useState<string | null>(null)

  const { providersStatus, isLoading: providersStatusLoading } =
    useModelProvidersStatus()

  const { chats, chatsLoading, chatsError, refetchChats } = useListChats(
    {
      workspaceId,
      entityType: "agent_preset",
      entityId: preset?.id,
      limit: 1,
    },
    { enabled: Boolean(preset && workspaceId) }
  )

  useEffect(() => {
    setCreatedChatId(null)
  }, [preset?.id])

  const existingChatId = chats?.[0]?.id
  const activeChatId = createdChatId ?? existingChatId

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

  const handleStartChat = async (forceNew = false) => {
    if (!preset || createChatPending || !providerReady) {
      return
    }

    if (!forceNew && activeChatId) {
      return
    }

    try {
      const newChat = await createChat({
        title: `${preset.name} chat`,
        entity_type: "agent_preset",
        entity_id: preset.id,
        tools: preset.actions ?? undefined,
      })
      setCreatedChatId(newChat.id)
      await refetchChats()
    } catch (error) {
      console.error("Failed to create agent preset chat", error)
    }
  }

  const handleResetChat = async () => {
    await handleStartChat(true)
  }

  const renderBody = () => {
    if (!preset) {
      return (
        <div className="flex h-full flex-col items-center justify-center gap-3 text-xs text-muted-foreground">
          <MessageCircle className="size-5" />
          <p>Select a saved preset to start a conversation.</p>
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
              This agent uses organization credentials for{" "}
              <span className="font-medium">{preset.model_provider}</span>.
              Configure them on the{" "}
              <Link
                href={`/organization/settings/agent`}
                className="font-medium text-primary hover:underline"
                target="_blank"
                rel="noopener noreferrer"
              >
                organization agent settings page
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

    if (!activeChatId) {
      return (
        <div className="flex h-full flex-col items-center justify-center gap-3 px-4 text-center text-xs text-muted-foreground">
          <Bot className="size-5 text-muted-foreground" />
          <p>Create a chat session to test this agent live.</p>
          <Button
            size="sm"
            onClick={() => void handleStartChat()}
            disabled={createChatPending || !canStartChat}
          >
            {createChatPending ? (
              <Loader2 className="mr-2 size-4 animate-spin" />
            ) : null}
            Start chat
          </Button>
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

    const shouldAutoFocusInput =
      Boolean(createdChatId) &&
      createdChatId === activeChatId &&
      (chat.messages?.length ?? 0) === 0

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
        autoFocusInput={shouldAutoFocusInput}
      />
    )
  }

  return (
    <div className="flex h-full flex-col bg-background">
      <div className="flex items-center justify-between border-b px-3 py-2">
        <div>
          <p className="text-xs text-muted-foreground">
            {preset ? `Chat with ${preset.name}` : "Select a preset to begin"}
          </p>
        </div>
        <div className="flex items-center gap-2">
          <Button
            size="sm"
            variant="ghost"
            onClick={() => void handleResetChat()}
            disabled={createChatPending || !canStartChat}
          >
            {createChatPending ? (
              <Loader2 className="mr-2 size-4 animate-spin" />
            ) : (
              <RotateCcw className="mr-2 size-4" />
            )}
            Reset chat
          </Button>
        </div>
      </div>
      <div className="flex-1 min-h-0">{renderBody()}</div>
    </div>
  )
}

function AgentPresetForm({
  preset,
  mode,
  onCreate,
  onUpdate,
  onDelete,
  isSaving,
  isDeleting,
  actionSuggestions,
  namespaceSuggestions,
  modelProviderOptions,
  modelOptionsByProvider,
}: {
  preset: AgentPresetRead | null
  mode: "create" | "edit"
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
}) {
  const slugEditedRef = useRef(mode === "edit")
  const [deleteDialogOpen, setDeleteDialogOpen] = useState(false)

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

  const {
    fields: headerFields,
    append: appendHeader,
    remove: removeHeader,
  } = useFieldArray({
    control: form.control,
    name: "mcpServerHeaders",
  })

  const {
    fields: settingsFields,
    append: appendSetting,
    remove: removeSetting,
  } = useFieldArray({
    control: form.control,
    name: "modelSettings",
  })

  useEffect(() => {
    const defaults = preset ? presetToFormValues(preset) : DEFAULT_FORM_VALUES
    form.reset(defaults, { keepDirty: false })
    slugEditedRef.current = mode === "edit"
  }, [form, mode, preset])

  const watchedName = form.watch("name")
  const providerValue = form.watch("model_provider")
  const outputTypeKind = form.watch("outputTypeKind")
  const modelOptions = modelOptionsByProvider[providerValue] ?? []

  useEffect(() => {
    if (mode === "create" && !slugEditedRef.current) {
      form.setValue("slug", slugify(watchedName ?? "", "-"), {
        shouldDirty: false,
      })
    }
  }, [form, mode, watchedName])

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
      slugEditedRef.current = true
    } else {
      const created = await onCreate(payload)
      form.reset(presetToFormValues(created))
      slugEditedRef.current = true
    }
  })

  const canSubmit =
    form.formState.isDirty ||
    (mode === "create" &&
      Boolean(form.watch("name")) &&
      Boolean(form.watch("model_provider")) &&
      Boolean(form.watch("model_name")))

  return (
    <div className="flex h-full flex-col">
      <div className="flex items-center justify-between border-b px-3 py-2">
        <div className="space-y-1">
          <h2 className="text-sm font-semibold">
            {mode === "edit"
              ? (preset?.name ?? "Agent preset")
              : "Create agent preset"}
          </h2>
        </div>
        <div className="flex items-center gap-2">
          <Button
            type="button"
            size="icon"
            onClick={() => handleSubmit()}
            disabled={isSaving || !canSubmit}
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
              onOpenChange={(nextOpen) => {
                if (isDeleting) {
                  return
                }
                setDeleteDialogOpen(nextOpen)
              }}
            >
              <DropdownMenu>
                <DropdownMenuTrigger asChild>
                  <Button
                    type="button"
                    variant="ghost"
                    size="icon"
                    disabled={isDeleting || isSaving}
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
                        setDeleteDialogOpen(true)
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
                  <AlertDialogTitle>Delete this agent preset?</AlertDialogTitle>
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
                    onClick={handleConfirmDelete}
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
      <ScrollArea className="flex-1">
        <Form {...form}>
          <form
            onSubmit={(event) => {
              event.preventDefault()
              void handleSubmit()
            }}
            className="flex flex-col gap-8 px-6 py-6 text-sm"
          >
            <section className="space-y-4">
              <FormField
                control={form.control}
                name="name"
                render={({ field }) => (
                  <FormItem>
                    <FormLabel>Name</FormLabel>
                    <FormControl>
                      <Input
                        placeholder="Security triage analyst"
                        {...field}
                        disabled={isSaving}
                      />
                    </FormControl>
                    <FormMessage />
                  </FormItem>
                )}
              />
              <FormField
                control={form.control}
                name="slug"
                render={({ field }) => (
                  <FormItem>
                    <FormLabel>Slug</FormLabel>
                    <FormControl>
                      <Input
                        placeholder="security-triage-analyst"
                        {...field}
                        onChange={(event) => {
                          slugEditedRef.current = true
                          field.onChange(event)
                        }}
                        disabled={isSaving}
                      />
                    </FormControl>
                    <FormDescription>
                      Used in workflow YAML and API calls. Lowercase and
                      hyphenated.
                    </FormDescription>
                    <FormMessage />
                  </FormItem>
                )}
              />
              <FormField
                control={form.control}
                name="description"
                render={({ field }) => (
                  <FormItem>
                    <FormLabel>Description</FormLabel>
                    <FormControl>
                      <Textarea
                        placeholder="Short summary of what this agent does."
                        rows={3}
                        {...field}
                        disabled={isSaving}
                      />
                    </FormControl>
                    <FormMessage />
                  </FormItem>
                )}
              />
            </section>

            <Separator />

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
                      <FormMessage />
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
                      <FormMessage />
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
                      <FormDescription>
                        Optional override for self-hosted or proxy deployments.
                      </FormDescription>
                      <FormMessage />
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
                      <FormDescription>
                        Number of automatic retries for transient errors.
                      </FormDescription>
                      <FormMessage />
                    </FormItem>
                  )}
                />
              </div>
            </section>

            <Separator />

            <section className="space-y-4">
              <FormField
                control={form.control}
                name="instructions"
                render={({ field }) => (
                  <FormItem>
                    <FormLabel>System prompt</FormLabel>
                    <FormControl>
                      <div className="min-h-[300px] rounded-md border border-input bg-background [&_.simple-editor-content_.tiptap.ProseMirror.simple-editor]:p-3">
                        <SimpleEditor
                          value={field.value ?? ""}
                          onChange={field.onChange}
                          onBlur={field.onBlur}
                          placeholder="You are a helpful analyst..."
                          editable={!isSaving}
                          className="min-h-[300px]"
                        />
                      </div>
                    </FormControl>
                    <FormDescription>
                      Markdown supported. Provide context, goals, and
                      guardrails.
                    </FormDescription>
                    <FormMessage />
                  </FormItem>
                )}
              />
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
                              Free-form text (default)
                            </SelectItem>
                            <SelectItem value="preset">
                              Structured primitive
                            </SelectItem>
                            <SelectItem value="json">
                              Custom JSON schema
                            </SelectItem>
                          </SelectContent>
                        </Select>
                      </FormControl>
                      <FormDescription>
                        Enforce structured responses when needed.
                      </FormDescription>
                    </FormItem>
                  )}
                />
                {outputTypeKind === "preset" ? (
                  <FormField
                    control={form.control}
                    name="outputTypePreset"
                    render={({ field }) => (
                      <FormItem>
                        <FormLabel>Preset type</FormLabel>
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
                              {PRESET_OUTPUT_TYPES.map((option) => (
                                <SelectItem
                                  key={option.value}
                                  value={option.value}
                                >
                                  {option.label}
                                </SelectItem>
                              ))}
                            </SelectContent>
                          </Select>
                        </FormControl>
                        <FormMessage />
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
                      <FormControl>
                        <Textarea
                          placeholder='{"type": "object", "properties": {...}}'
                          rows={8}
                          className="font-mono text-xs"
                          {...field}
                          disabled={isSaving}
                        />
                      </FormControl>
                      <FormDescription>
                        Provide a JSON schema describing the desired response.
                      </FormDescription>
                      <FormMessage />
                    </FormItem>
                  )}
                />
              ) : null}
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
                        placeholder="Add tools by action id"
                        searchKeys={["label", "value", "description", "group"]}
                        allowCustomTags
                        disabled={isSaving}
                      />
                    </FormControl>
                    <FormDescription>
                      Registry action identifiers (e.g. core.http_request).
                    </FormDescription>
                    <FormMessage />
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
                    <FormDescription>
                      Limit dynamic tool discovery to selected namespaces.
                    </FormDescription>
                    <FormMessage />
                  </FormItem>
                )}
              />
              <div className="space-y-3">
                <div className="flex items-center justify-between">
                  <label className="text-sm font-medium leading-none">
                    Approval rules
                  </label>
                  <Button
                    type="button"
                    size="sm"
                    variant="outline"
                    onClick={() =>
                      appendToolApproval({
                        tool: "",
                        allow: true,
                      })
                    }
                    disabled={isSaving}
                  >
                    <Plus className="mr-2 size-4" />
                    Add rule
                  </Button>
                </div>
                {toolApprovalFields.length === 0 ? (
                  <p className="rounded-md border border-dashed px-3 py-4 text-xs text-muted-foreground">
                    No manual approval rules yet. Add a tool to require human
                    review or to force manual overrides.
                  </p>
                ) : (
                  <div className="space-y-3">
                    {/* Column headers */}
                    <div className="grid gap-3 px-3 md:grid-cols-[minmax(0,1fr)_220px_auto] md:items-center">
                      <div className="text-xs font-medium uppercase text-muted-foreground">
                        Tool
                      </div>
                      <div className="text-xs font-medium uppercase text-muted-foreground md:text-center">
                        Manual approval
                      </div>
                      <div className="w-10" aria-hidden="true" />
                    </div>

                    {/* Content rows */}
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
                                      suggestions={actionSuggestions}
                                      searchKeys={[
                                        "label",
                                        "value",
                                        "description",
                                        "group",
                                      ]}
                                      placeholder="Select an action..."
                                      disabled={isSaving}
                                    />
                                  </FormControl>
                                  <FormMessage />
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
                                        {field.value
                                          ? "Required"
                                          : "Not required"}
                                      </span>
                                    </div>
                                  </FormControl>
                                  <FormMessage />
                                </FormItem>
                              )}
                            />
                            <Button
                              type="button"
                              variant="ghost"
                              size="icon"
                              className="justify-self-start self-start text-muted-foreground md:justify-self-end"
                              onClick={() => removeToolApproval(index)}
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
              </div>
            </section>

            <Separator />

            <section className="space-y-4">
              <FormField
                control={form.control}
                name="mcpServerUrl"
                render={({ field }) => (
                  <FormItem>
                    <FormLabel>MCP server URL</FormLabel>
                    <FormControl>
                      <Input
                        placeholder="https://mcp.example.com"
                        value={field.value ?? ""}
                        onChange={field.onChange}
                        disabled={isSaving}
                      />
                    </FormControl>
                    <FormDescription>
                      Optional Model Context Protocol server for toolsets.
                    </FormDescription>
                    <FormMessage />
                  </FormItem>
                )}
              />
              <KeyValueFieldArray
                title="MCP headers"
                description="Optional headers sent with MCP requests."
                fields={headerFields}
                control={form.control}
                name="mcpServerHeaders"
                onAdd={() => appendHeader({ key: "", value: "" })}
                onRemove={removeHeader}
                keyPlaceholder="X-API-KEY"
                valuePlaceholder="secret"
                disabled={isSaving}
              />
              <KeyValueFieldArray
                title="Model settings"
                description="Extra arguments passed to the model (temperature, max_tokens, etc)."
                fields={settingsFields}
                control={form.control}
                name="modelSettings"
                onAdd={() => appendSetting({ key: "", value: "" })}
                onRemove={removeSetting}
                keyPlaceholder="temperature"
                valuePlaceholder="0.2"
                disabled={isSaving}
              />
            </section>
          </form>
        </Form>
      </ScrollArea>
    </div>
  )
}

function KeyValueFieldArray({
  title,
  description,
  fields,
  control,
  name,
  onAdd,
  onRemove,
  keyPlaceholder,
  valuePlaceholder,
  disabled,
}: {
  title: string
  description?: string
  fields: { id: string }[]
  control: Control<AgentPresetFormValues>
  name: "mcpServerHeaders" | "modelSettings"
  onAdd: () => void
  onRemove: (index: number) => void
  keyPlaceholder?: string
  valuePlaceholder?: string
  disabled?: boolean
}) {
  const listId = useId()

  return (
    <div className="space-y-3">
      <div className="flex items-center justify-between">
        <label className="text-sm font-medium leading-none">{title}</label>
        <Button
          type="button"
          size="sm"
          variant="outline"
          onClick={onAdd}
          disabled={disabled}
        >
          <Plus className="mr-2 size-4" />
          Add
        </Button>
      </div>
      {fields.length === 0 ? (
        <div className="space-y-1.5">
          <p className="rounded-md border border-dashed px-3 py-4 text-xs text-muted-foreground">
            No entries yet.
          </p>
          {description ? (
            <p className="text-[0.8rem] text-muted-foreground">{description}</p>
          ) : null}
        </div>
      ) : (
        <div className="space-y-2">
          {fields.map((field, index) => (
            <div
              key={field.id}
              className="grid gap-2 rounded-md border px-3 py-3 md:grid-cols-[1fr_1fr_auto]"
            >
              <FormField
                control={control}
                name={`${name}.${index}.key`}
                render={({ field: innerField }) => (
                  <FormItem>
                    <FormLabel className="text-xs uppercase text-muted-foreground">
                      Key
                    </FormLabel>
                    <FormControl>
                      <Input
                        placeholder={keyPlaceholder}
                        value={String(innerField.value ?? "")}
                        onChange={innerField.onChange}
                        onBlur={innerField.onBlur}
                        name={innerField.name}
                        ref={innerField.ref}
                        disabled={disabled}
                        list={`${listId}-${name}-key`}
                      />
                    </FormControl>
                    <FormMessage />
                  </FormItem>
                )}
              />
              <FormField
                control={control}
                name={`${name}.${index}.value`}
                render={({ field: innerField }) => (
                  <FormItem>
                    <FormLabel className="text-xs uppercase text-muted-foreground">
                      Value
                    </FormLabel>
                    <FormControl>
                      <Input
                        placeholder={valuePlaceholder}
                        value={String(innerField.value ?? "")}
                        onChange={innerField.onChange}
                        onBlur={innerField.onBlur}
                        name={innerField.name}
                        ref={innerField.ref}
                        disabled={disabled}
                      />
                    </FormControl>
                    <FormMessage />
                  </FormItem>
                )}
              />
              <div className="flex items-end justify-end">
                <Button
                  type="button"
                  variant="ghost"
                  size="icon"
                  onClick={() => onRemove(index)}
                  disabled={disabled}
                  aria-label={`Remove ${title}`}
                >
                  <Trash2 className="size-4" />
                </Button>
              </div>
            </div>
          ))}
          {description ? (
            <p className="text-[0.8rem] text-muted-foreground">{description}</p>
          ) : null}
        </div>
      )}
      <datalist id={`${listId}-${name}-key`}>
        {name === "modelSettings" ? (
          <>
            <option value="temperature" />
            <option value="max_tokens" />
            <option value="top_p" />
            <option value="response_format" />
          </>
        ) : null}
      </datalist>
    </div>
  )
}

function AgentPresetBuilderChatPane({
  preset,
  workspaceId,
}: {
  preset: AgentPresetRead | null
  workspaceId: string
}) {
  const presetId = preset?.id
  const [createdChatId, setCreatedChatId] = useState<string | null>(null)

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
      limit: 1,
    },
    { enabled: Boolean(presetId && workspaceId) }
  )

  useEffect(() => {
    setCreatedChatId(null)
  }, [presetId])

  const existingChatId = chats?.[0]?.id
  const activeChatId = createdChatId ?? existingChatId

  const { createChat, createChatPending } = useCreateChat(workspaceId)
  const { chat, chatLoading, chatError } = useGetChatVercel({
    chatId: activeChatId,
    workspaceId,
  })

  const canStartChat = Boolean(presetId && chatReady && modelInfo)

  const handleStartChat = async (forceNew = false) => {
    if (!preset || !presetId || createChatPending || !chatReady || !modelInfo) {
      return
    }

    if (!forceNew && activeChatId) {
      return
    }

    try {
      const newChat = await createChat({
        title: `${preset.name} builder assistant`,
        entity_type: "agent_preset_builder",
        entity_id: presetId,
      })
      setCreatedChatId(newChat.id)
      await refetchChats()
    } catch (error) {
      console.error("Failed to create builder assistant chat", error)
    }
  }

  const handleResetChat = async () => {
    if (!canStartChat) {
      return
    }
    await handleStartChat(true)
  }

  const renderBody = () => {
    if (!presetId) {
      return (
        <div className="flex h-full flex-col items-center justify-center gap-3 px-4 text-center text-xs text-muted-foreground">
          <MessageCircle className="size-5" />
          <p>Save this preset to chat with the builder assistant.</p>
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
        <div className="flex h-full flex-col items-center justify-center gap-3 px-4 text-center text-xs text-muted-foreground">
          <MessageCircle className="size-5 text-muted-foreground" />
          <p>
            Ask the assistant for prompt, tool, or approval suggestions to get
            started.
          </p>
          <Button
            size="sm"
            onClick={() => void handleStartChat()}
            disabled={createChatPending || !canStartChat}
          >
            {createChatPending ? (
              <Loader2 className="mr-2 size-4 animate-spin" />
            ) : null}
            Start assistant
          </Button>
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
        autoFocusInput={
          Boolean(createdChatId) &&
          createdChatId === activeChatId &&
          (chat.messages?.length ?? 0) === 0
        }
      />
    )
  }

  return (
    <div className="flex h-full flex-col bg-background">
      <div className="flex items-center justify-between border-b px-3 py-2">
        <div>
          <h3 className="text-sm font-semibold">Builder assistant</h3>
        </div>
        <div className="flex items-center gap-2">
          <Button
            size="sm"
            variant="ghost"
            onClick={() => void handleResetChat()}
            disabled={createChatPending || !canStartChat}
          >
            {createChatPending ? (
              <Loader2 className="mr-2 size-4 animate-spin" />
            ) : (
              <RotateCcw className="mr-2 size-4" />
            )}
            Reset assistant
          </Button>
        </div>
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
        ? "preset"
        : "json"
      : "none",
    outputTypePreset: typeof outputType === "string" ? outputType : "",
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
    mcpServerUrl: preset.mcp_server_url ?? "",
    mcpServerHeaders: preset.mcp_server_headers
      ? Object.entries(preset.mcp_server_headers).map(
          ([key, value]): KeyValueFormValue => ({
            key,
            value: value ?? "",
          })
        )
      : [],
    modelSettings: preset.model_settings
      ? Object.entries(preset.model_settings).map(
          ([key, value]): KeyValueFormValue => ({
            key,
            value: typeof value === "string" ? value : JSON.stringify(value),
          })
        )
      : [],
    retries: preset.retries ?? DEFAULT_RETRIES,
  }
}

function formValuesToPayload(values: AgentPresetFormValues): AgentPresetCreate {
  const outputType =
    values.outputTypeKind === "none"
      ? null
      : values.outputTypeKind === "preset"
        ? (values.outputTypePreset ?? null)
        : values.outputTypeJson
          ? JSON.parse(values.outputTypeJson)
          : null

  const headers = keyValueArrayToRecord<string>(values.mcpServerHeaders)
  const settings = keyValueArrayToRecord(values.modelSettings, parseMaybeJson)

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
    tool_approvals: toToolApprovalMap(values.toolApprovals),
    mcp_server_url: normalizeOptional(values.mcpServerUrl),
    mcp_server_headers: headers ?? null,
    model_settings: settings,
    retries: values.retries,
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

function keyValueArrayToRecord<T = string>(
  entries: KeyValueFormValue[],
  transform: (value: string) => T = (value) => value as T
): Record<string, T> | null {
  const result: Record<string, T> = {}
  for (const entry of entries) {
    const key = entry.key.trim()
    if (!key) continue
    result[key] = transform(entry.value ?? "")
  }
  return Object.keys(result).length > 0 ? result : null
}

function parseMaybeJson(value: string) {
  const trimmed = value.trim()
  if (!trimmed) {
    return ""
  }
  try {
    return JSON.parse(trimmed)
  } catch (_error) {
    return trimmed
  }
}
