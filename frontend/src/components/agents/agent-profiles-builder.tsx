"use client"

import { zodResolver } from "@hookform/resolvers/zod"
import {
  AlertCircle,
  Bot,
  Loader2,
  MessageCircle,
  Plus,
  RotateCcw,
  Trash2,
} from "lucide-react"
import { useEffect, useId, useMemo, useRef, useState } from "react"
import {
  type Control,
  type FieldPath,
  useFieldArray,
  useForm,
} from "react-hook-form"
import { z } from "zod"
import type {
  AgentProfileCreate,
  AgentProfileRead,
  AgentProfileUpdate,
  ChatEntity,
} from "@/client"
import { ChatSessionPane } from "@/components/chat/chat-session-pane"
import { CenteredSpinner } from "@/components/loading/spinner"
import { MultiTagCommandInput, type Suggestion } from "@/components/tags-input"
import { SimpleEditor } from "@/components/tiptap-templates/simple/simple-editor"
import { Alert, AlertDescription, AlertTitle } from "@/components/ui/alert"
import { Badge } from "@/components/ui/badge"
import { Button } from "@/components/ui/button"
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
  useAgentProfiles,
  useCreateAgentProfile,
  useDeleteAgentProfile,
  useUpdateAgentProfile,
} from "@/hooks"
import { useCreateChat, useGetChatVercel, useListChats } from "@/hooks/use-chat"
import type { ModelInfo } from "@/lib/chat"
import {
  useAgentModels,
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

const NEW_PROFILE_ID = "__new__"
const DEFAULT_RETRIES = 3

const agentProfileSchema = z
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

type AgentProfileFormValues = z.infer<typeof agentProfileSchema>
type ToolApprovalFormValue = AgentProfileFormValues["toolApprovals"][number]
type KeyValueFormValue = AgentProfileFormValues["modelSettings"][number]

const DEFAULT_FORM_VALUES: AgentProfileFormValues = {
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

export function AgentProfilesBuilder() {
  const workspaceId = useWorkspaceId()
  const { profiles, profilesIsLoading, profilesError } =
    useAgentProfiles(workspaceId)
  const { registryActions } = useRegistryActions()
  const { providers } = useModelProviders()
  const { models } = useAgentModels()
  const [sidebarTab, setSidebarTab] = useState<"profiles" | "chat">("profiles")
  const { createAgentProfile, createAgentProfileIsPending } =
    useCreateAgentProfile(workspaceId)
  const { updateAgentProfile, updateAgentProfileIsPending } =
    useUpdateAgentProfile(workspaceId)
  const { deleteAgentProfile, deleteAgentProfileIsPending } =
    useDeleteAgentProfile(workspaceId)

  const [selectedProfileId, setSelectedProfileId] =
    useState<string>(NEW_PROFILE_ID)
  const [optimisticProfile, setOptimisticProfile] =
    useState<AgentProfileRead | null>(null)

  const combinedProfiles = useMemo(() => {
    if (!profiles) {
      return optimisticProfile ? [optimisticProfile] : []
    }
    if (
      optimisticProfile &&
      !profiles.some((profile) => profile.id === optimisticProfile.id)
    ) {
      return [optimisticProfile, ...profiles]
    }
    return profiles
  }, [optimisticProfile, profiles])

  useEffect(() => {
    if (!profiles || profiles.length === 0) {
      setSelectedProfileId(NEW_PROFILE_ID)
      return
    }

    if (selectedProfileId === NEW_PROFILE_ID) {
      return
    }

    const exists = profiles.some((profile) => profile.id === selectedProfileId)
    if (!exists) {
      setSelectedProfileId(profiles[0]?.id ?? NEW_PROFILE_ID)
    }
  }, [profiles, selectedProfileId])

  useEffect(() => {
    if (!optimisticProfile || !profiles) {
      return
    }
    const synced = profiles.some(
      (profile) => profile.id === optimisticProfile.id
    )
    if (synced) {
      setOptimisticProfile(null)
    }
  }, [optimisticProfile, profiles])

  const selectedProfile =
    selectedProfileId === NEW_PROFILE_ID
      ? null
      : (combinedProfiles.find((profile) => profile.id === selectedProfileId) ??
        null)

  const chatTabDisabled = !selectedProfile

  useEffect(() => {
    if (chatTabDisabled && sidebarTab === "chat") {
      setSidebarTab("profiles")
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
      return {} as Record<string, { label: string; value: string }[]>
    }
    const grouped = {} as Record<string, { label: string; value: string }[]>
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

  if (profilesIsLoading) {
    return <CenteredSpinner />
  }

  if (profilesError) {
    const detail =
      typeof profilesError.body?.detail === "string"
        ? profilesError.body.detail
        : profilesError.message
    return (
      <div className="flex h-full items-center justify-center px-6">
        <Alert variant="destructive" className="max-w-xl">
          <AlertTitle>Unable to load agent profiles</AlertTitle>
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
                setSidebarTab(value as "profiles" | "chat")
              }}
              className="flex h-full flex-col"
            >
              <div className="px-3 pt-3">
                <TabsList className="grid h-9 w-full grid-cols-2">
                  <TabsTrigger value="profiles">Profiles</TabsTrigger>
                  <TabsTrigger value="chat" disabled={chatTabDisabled}>
                    Live chat
                  </TabsTrigger>
                </TabsList>
              </div>
              <div className="flex-1 min-h-0">
                <TabsContent
                  value="profiles"
                  className="h-full flex-col px-0 py-0 data-[state=active]:flex data-[state=inactive]:hidden"
                >
                  <ProfilesSidebar
                    profiles={combinedProfiles}
                    selectedId={selectedProfileId}
                    onSelect={(id) => setSelectedProfileId(id)}
                    onCreate={() => setSelectedProfileId(NEW_PROFILE_ID)}
                  />
                </TabsContent>
                <TabsContent
                  value="chat"
                  className="h-full flex-col px-0 py-0 data-[state=active]:flex data-[state=inactive]:hidden"
                >
                  <AgentProfileChatPane
                    profile={selectedProfile}
                    workspaceId={workspaceId}
                  />
                </TabsContent>
              </div>
            </Tabs>
          </div>
        </ResizablePanel>
        <ResizableHandle withHandle />
        <ResizablePanel defaultSize={50} minSize={34}>
          <AgentProfileForm
            key={selectedProfile?.id ?? NEW_PROFILE_ID}
            profile={selectedProfile}
            mode={selectedProfile ? "edit" : "create"}
            actionSuggestions={actionSuggestions}
            namespaceSuggestions={namespaceSuggestions}
            modelOptionsByProvider={modelOptionsByProvider}
            modelProviderOptions={modelProviderOptions}
            isSaving={
              selectedProfile
                ? updateAgentProfileIsPending
                : createAgentProfileIsPending
            }
            isDeleting={deleteAgentProfileIsPending}
            onCreate={async (payload) => {
              const created = await createAgentProfile(payload)
              setOptimisticProfile(created)
              setSelectedProfileId(created.id)
              return created
            }}
            onUpdate={async (profileId, payload) => {
              const updated = await updateAgentProfile({
                profileId,
                ...payload,
              })
              setOptimisticProfile(updated)
              setSelectedProfileId(updated.id)
              return updated
            }}
            onDelete={
              selectedProfile
                ? async () => {
                    await deleteAgentProfile({
                      profileId: selectedProfile.id,
                      profileName: selectedProfile.name,
                    })
                    setOptimisticProfile(null)
                    const remaining =
                      profiles?.filter(
                        (profile) => profile.id !== selectedProfile.id
                      ) ?? []
                    if (remaining.length > 0) {
                      setSelectedProfileId(remaining[0].id)
                    } else {
                      setSelectedProfileId(NEW_PROFILE_ID)
                    }
                  }
                : undefined
            }
          />
        </ResizablePanel>
        <ResizableHandle withHandle />
        <ResizablePanel defaultSize={30} minSize={20}>
          <AgentProfileBuilderChatPane
            profile={selectedProfile}
            workspaceId={workspaceId}
          />
        </ResizablePanel>
      </ResizablePanelGroup>
    </div>
  )
}

function ProfilesSidebar({
  profiles,
  selectedId,
  onSelect,
  onCreate,
}: {
  profiles: AgentProfileRead[] | null
  selectedId: string
  onSelect: (id: string) => void
  onCreate: () => void
}) {
  const isCreating = selectedId === NEW_PROFILE_ID
  const list = profiles ?? []

  return (
    <div className="flex h-full flex-col">
      <div className="flex items-center justify-between border-b px-3 py-2">
        <div>
          <h2 className="text-sm font-semibold tracking-tight">
            Agent profiles
          </h2>
          <p className="text-xs text-muted-foreground">{list.length} saved</p>
        </div>
        <Button
          size="sm"
          onClick={onCreate}
          variant={isCreating ? "default" : "secondary"}
        >
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
                <span>No saved profiles yet.</span>
                <span>
                  Create one to reuse agents across workflows and chat.
                </span>
              </div>
            ) : (
              list.map((profile) => (
                <SidebarItem
                  key={profile.id}
                  active={selectedId === profile.id}
                  onClick={() => onSelect(profile.id)}
                  title={profile.name}
                  description={profile.slug}
                  status={profile.model_name}
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
  onClick,
  title,
  description,
  status,
}: {
  active?: boolean
  onClick: () => void
  title: string
  description?: string | null
  status?: string | null
}) {
  return (
    <button
      type="button"
      onClick={onClick}
      className={cn(
        "w-full rounded-md border px-3 py-2.5 text-left transition-colors",
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
    </button>
  )
}

function AgentProfileChatPane({
  profile,
  workspaceId,
}: {
  profile: AgentProfileRead | null
  workspaceId: string
}) {
  const [createdChatId, setCreatedChatId] = useState<string | null>(null)

  const { providersStatus, isLoading: providersStatusLoading } =
    useModelProvidersStatus()

  const { chats, chatsLoading, chatsError, refetchChats } = useListChats(
    {
      workspaceId,
      entityType: "agent_profile" as ChatEntity,
      entityId: profile?.id,
      limit: 1,
    },
    { enabled: Boolean(profile && workspaceId) }
  )

  useEffect(() => {
    setCreatedChatId(null)
  }, [profile?.id])

  const existingChatId = chats?.[0]?.id
  const activeChatId = createdChatId ?? existingChatId

  const { createChat, createChatPending } = useCreateChat(workspaceId)
  const { chat, chatLoading, chatError } = useGetChatVercel({
    chatId: activeChatId,
    workspaceId,
  })

  const modelInfo: ModelInfo | null = useMemo(() => {
    if (!profile) {
      return null
    }
    return {
      name: profile.model_name,
      provider: profile.model_provider,
      baseUrl: profile.base_url ?? null,
    }
  }, [profile])

  const providerReady = useMemo(() => {
    if (!profile) {
      return false
    }
    return providersStatus?.[profile.model_provider] ?? false
  }, [providersStatus, profile])

  const canStartChat = Boolean(profile && providerReady)

  const handleStartChat = async (forceNew = false) => {
    if (!profile || createChatPending || !providerReady) {
      return
    }

    if (!forceNew && activeChatId) {
      return
    }

    try {
      const newChat = await createChat({
        title: `${profile.name} chat`,
        entity_type: "agent_profile" as ChatEntity,
        entity_id: profile.id,
        tools: profile.actions ?? undefined,
      })
      setCreatedChatId(newChat.id)
      await refetchChats()
    } catch (error) {
      console.error("Failed to create agent profile chat", error)
    }
  }

  const handleResetChat = async () => {
    await handleStartChat(true)
  }

  const renderBody = () => {
    if (!profile) {
      return (
        <div className="flex h-full flex-col items-center justify-center gap-3 text-xs text-muted-foreground">
          <MessageCircle className="size-5" />
          <p>Select a saved profile to start a conversation.</p>
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
        <div className="flex h-full flex-col items-center justify-center gap-2 px-4 text-center text-xs text-muted-foreground">
          <AlertCircle className="size-5 text-amber-500" />
          <p>
            Configure credentials for{" "}
            <span className="font-medium">{profile.model_provider}</span> to
            chat with this agent.
          </p>
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

    return (
      <ChatSessionPane
        chat={chat}
        workspaceId={workspaceId}
        entityType={"agent_profile"}
        entityId={profile.id}
        className="flex-1 min-h-0"
        placeholder={`Talk to ${profile.name}...`}
        modelInfo={modelInfo}
      />
    )
  }

  return (
    <div className="flex h-full flex-col bg-background">
      <div className="flex items-center justify-between border-b px-3 py-2">
        <div>
          <h3 className="text-sm font-semibold">Interactive session</h3>
          <p className="text-xs text-muted-foreground">
            {profile
              ? `Chat with ${profile.name}`
              : "Select a profile to begin"}
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

function AgentProfileForm({
  profile,
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
  profile: AgentProfileRead | null
  mode: "create" | "edit"
  onCreate: (payload: AgentProfileCreate) => Promise<AgentProfileRead>
  onUpdate: (
    profileId: string,
    payload: AgentProfileUpdate
  ) => Promise<AgentProfileRead>
  onDelete?: () => Promise<void>
  isSaving: boolean
  isDeleting: boolean
  actionSuggestions: Suggestion[]
  namespaceSuggestions: Suggestion[]
  modelProviderOptions: string[]
  modelOptionsByProvider: Record<string, { label: string; value: string }[]>
}) {
  const slugEditedRef = useRef(mode === "edit")

  const form = useForm<AgentProfileFormValues>({
    resolver: zodResolver(agentProfileSchema),
    mode: "onBlur",
    defaultValues: profile ? profileToFormValues(profile) : DEFAULT_FORM_VALUES,
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
    const defaults = profile
      ? profileToFormValues(profile)
      : DEFAULT_FORM_VALUES
    form.reset(defaults, { keepDirty: false })
    slugEditedRef.current = mode === "edit"
  }, [form, mode, profile])

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
    if (mode === "edit" && profile) {
      const updated = await onUpdate(profile.id, payload)
      form.reset(profileToFormValues(updated))
      slugEditedRef.current = true
    } else {
      const created = await onCreate(payload)
      form.reset(profileToFormValues(created))
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
      <div className="flex items-center justify-between border-b px-4 py-3">
        <div className="space-y-1">
          <h2 className="text-sm font-semibold">
            {mode === "edit"
              ? (profile?.name ?? "Agent profile")
              : "Create agent profile"}
          </h2>
          <p className="text-xs text-muted-foreground">
            Configure prompts, tools, models, and approvals for reusable agents.
          </p>
        </div>
        <div className="flex items-center gap-2">
          {mode === "edit" && onDelete ? (
            <Button
              type="button"
              variant="outline"
              size="sm"
              onClick={async () => {
                if (
                  confirm("Delete this agent profile? This cannot be undone.")
                ) {
                  await onDelete()
                }
              }}
              disabled={isDeleting || isSaving}
            >
              <Trash2 className="mr-2 size-4" />
              Delete
            </Button>
          ) : null}
          <Button
            type="button"
            size="sm"
            onClick={() => handleSubmit()}
            disabled={isSaving || !canSubmit}
          >
            {isSaving ? <Loader2 className="mr-2 size-4 animate-spin" /> : null}
            {mode === "edit" ? "Save changes" : "Create profile"}
          </Button>
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
              <header className="space-y-1">
                <h3 className="text-sm font-semibold">Profile details</h3>
                <p className="text-xs text-muted-foreground">
                  Basic metadata for identifying this agent across Tracecat.
                </p>
              </header>
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
              <header className="space-y-1">
                <h3 className="text-sm font-semibold">Model & runtime</h3>
                <p className="text-xs text-muted-foreground">
                  Choose the foundation model, optional overrides, and retries.
                </p>
              </header>
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
              <header className="space-y-1">
                <h3 className="text-sm font-semibold">Prompt & behavior</h3>
                <p className="text-xs text-muted-foreground">
                  Define instructions and expected output shape.
                </p>
              </header>
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
              <header className="space-y-1">
                <h3 className="text-sm font-semibold">Tools & approvals</h3>
                <p className="text-xs text-muted-foreground">
                  Declare tool access and manual approval requirements.
                </p>
              </header>
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
                        className="border"
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
                        className="border"
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
                  <h4 className="text-xs font-semibold uppercase text-muted-foreground">
                    Approval rules
                  </h4>
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
                    {toolApprovalFields.map((item, index) => (
                      <div
                        key={item.id}
                        className="rounded-md border px-3 py-3"
                      >
                        <div className="flex flex-col gap-3 md:flex-row md:items-center">
                          <FormField
                            control={form.control}
                            name={
                              `toolApprovals.${index}.tool` as FieldPath<AgentProfileFormValues>
                            }
                            render={({ field }) => (
                              <FormItem className="flex-1">
                                <FormLabel className="text-xs uppercase text-muted-foreground">
                                  Tool ID
                                </FormLabel>
                                <FormControl>
                                  <Input
                                    placeholder="core.http_request"
                                    value={String(field.value ?? "")}
                                    onChange={field.onChange}
                                    onBlur={field.onBlur}
                                    name={field.name}
                                    ref={field.ref}
                                    disabled={isSaving}
                                    list={`agent-tool-options-${index}`}
                                  />
                                </FormControl>
                                <FormMessage />
                                <datalist id={`agent-tool-options-${index}`}>
                                  {actionSuggestions.map((suggestion) => (
                                    <option
                                      key={suggestion.value}
                                      value={suggestion.value}
                                    >
                                      {suggestion.label}
                                    </option>
                                  ))}
                                </datalist>
                              </FormItem>
                            )}
                          />
                          <FormField
                            control={form.control}
                            name={
                              `toolApprovals.${index}.allow` as FieldPath<AgentProfileFormValues>
                            }
                            render={({ field }) => (
                              <FormItem className="flex flex-row items-center gap-2 space-y-0">
                                <FormControl>
                                  <Switch
                                    checked={Boolean(field.value)}
                                    onCheckedChange={field.onChange}
                                    disabled={isSaving}
                                  />
                                </FormControl>
                                <FormDescription className="text-xs">
                                  Allow without manual approval
                                </FormDescription>
                              </FormItem>
                            )}
                          />
                          <Button
                            type="button"
                            variant="ghost"
                            size="icon"
                            className="self-start text-muted-foreground"
                            onClick={() => removeToolApproval(index)}
                            disabled={isSaving}
                            aria-label="Remove approval rule"
                          >
                            <Trash2 className="size-4" />
                          </Button>
                        </div>
                      </div>
                    ))}
                  </div>
                )}
              </div>
            </section>

            <Separator />

            <section className="space-y-4">
              <header className="space-y-1">
                <h3 className="text-sm font-semibold">Advanced</h3>
                <p className="text-xs text-muted-foreground">
                  Configure MCP servers, custom headers, and model arguments.
                </p>
              </header>
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
  control: Control<AgentProfileFormValues>
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
        <div>
          <h4 className="text-xs font-semibold uppercase text-muted-foreground">
            {title}
          </h4>
          {description ? (
            <p className="text-xs text-muted-foreground">{description}</p>
          ) : null}
        </div>
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
        <p className="rounded-md border border-dashed px-3 py-4 text-xs text-muted-foreground">
          No entries yet.
        </p>
      ) : (
        <div className="space-y-2">
          {fields.map((field, index) => (
            <div
              key={field.id}
              className="grid gap-2 rounded-md border px-3 py-3 md:grid-cols-[1fr_1fr_auto]"
            >
              <FormField
                control={control}
                name={
                  `${name}.${index}.key` as FieldPath<AgentProfileFormValues>
                }
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
                name={
                  `${name}.${index}.value` as FieldPath<AgentProfileFormValues>
                }
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

function AgentProfileBuilderChatPane({
  profile,
  workspaceId,
}: {
  profile: AgentProfileRead | null
  workspaceId: string
}) {
  const profileId = profile?.id
  const [createdChatId, setCreatedChatId] = useState<string | null>(null)

  const { providersStatus, isLoading: providersStatusLoading } =
    useModelProvidersStatus()

  const { chats, chatsLoading, chatsError, refetchChats } = useListChats(
    {
      workspaceId,
      entityType: "agent_profile_builder" as ChatEntity,
      entityId: profileId ?? undefined,
      limit: 1,
    },
    { enabled: Boolean(profileId && workspaceId) }
  )

  useEffect(() => {
    setCreatedChatId(null)
  }, [profileId])

  const existingChatId = chats?.[0]?.id
  const activeChatId = createdChatId ?? existingChatId

  const { createChat, createChatPending } = useCreateChat(workspaceId)
  const { chat, chatLoading, chatError } = useGetChatVercel({
    chatId: activeChatId,
    workspaceId,
  })

  const modelInfo: ModelInfo | null = useMemo(() => {
    if (!profile) {
      return null
    }
    return {
      name: profile.model_name,
      provider: profile.model_provider,
      baseUrl: profile.base_url ?? null,
    }
  }, [profile])

  const providerReady = useMemo(() => {
    if (!profileId) {
      return false
    }
    const provider = profile?.model_provider ?? ""
    return providersStatus?.[provider] ?? false
  }, [providersStatus, profile?.model_provider, profileId])

  const canStartChat = Boolean(profileId && providerReady)

  const handleStartChat = async (forceNew = false) => {
    if (!profile || !profileId || createChatPending || !providerReady) {
      return
    }

    if (!forceNew && activeChatId) {
      return
    }

    try {
      const newChat = await createChat({
        title: `${profile.name} builder assistant`,
        entity_type: "agent_profile_builder" as ChatEntity,
        entity_id: profileId,
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
    if (!profileId) {
      return (
        <div className="flex h-full flex-col items-center justify-center gap-3 px-4 text-center text-xs text-muted-foreground">
          <MessageCircle className="size-5" />
          <p>Save this profile to chat with the builder assistant.</p>
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
        <div className="flex h-full flex-col items-center justify-center gap-2 px-4 text-center text-xs text-muted-foreground">
          <AlertCircle className="size-5 text-amber-500" />
          <p>
            Configure credentials for{" "}
            <span className="font-medium">
              {profile?.model_provider ?? "this provider"}
            </span>{" "}
            to enable the builder assistant.
          </p>
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
          <p>Ask the assistant for prompt suggestions to get started.</p>
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
        entityType={"agent_profile_builder"}
        entityId={profileId}
        className="flex-1 min-h-0"
        placeholder={`Ask ${profile?.name ?? "the assistant"} to refine the system prompt...`}
        modelInfo={modelInfo}
      />
    )
  }

  return (
    <div className="flex h-full flex-col bg-background">
      <div className="flex items-center justify-between border-b px-3 py-2">
        <div>
          <h3 className="text-sm font-semibold">Builder assistant</h3>
          <p className="text-xs text-muted-foreground">
            Get help drafting or refining this agent&apos;s system prompt.
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
            Reset assistant
          </Button>
        </div>
      </div>
      <div className="flex-1 min-h-0">{renderBody()}</div>
    </div>
  )
}

function profileToFormValues(
  profile: AgentProfileRead
): AgentProfileFormValues {
  const outputType =
    profile.output_type === null || profile.output_type === undefined
      ? null
      : profile.output_type

  return {
    name: profile.name,
    slug: profile.slug,
    description: profile.description ?? "",
    instructions: profile.instructions ?? "",
    model_provider: profile.model_provider,
    model_name: profile.model_name,
    base_url: profile.base_url ?? "",
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
    actions: profile.actions ?? [],
    namespaces: profile.namespaces ?? [],
    toolApprovals: profile.tool_approvals
      ? Object.entries(profile.tool_approvals).map(
          ([tool, allow]): ToolApprovalFormValue => ({
            tool,
            allow: Boolean(allow),
          })
        )
      : [],
    mcpServerUrl: profile.mcp_server_url ?? "",
    mcpServerHeaders: profile.mcp_server_headers
      ? Object.entries(profile.mcp_server_headers).map(
          ([key, value]): KeyValueFormValue => ({
            key,
            value: value ?? "",
          })
        )
      : [],
    modelSettings: profile.model_settings
      ? Object.entries(profile.model_settings).map(
          ([key, value]): KeyValueFormValue => ({
            key,
            value: typeof value === "string" ? value : JSON.stringify(value),
          })
        )
      : [],
    retries: profile.retries ?? DEFAULT_RETRIES,
  }
}

function formValuesToPayload(
  values: AgentProfileFormValues
): AgentProfileCreate {
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
  transform: (value: string) => T = (value) => value as unknown as T
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
