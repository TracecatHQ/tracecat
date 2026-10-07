"use client"

import { useState } from "react"
import {
  type AgentSessionReadVercel,
  type MCPIntegrationRead,
  type RegistryActionReadMinimal,
  type WorkspaceChatOverrides,
  workspacesGetWorkspace,
} from "@/client"
import { toast } from "@/components/ui/use-toast"
import { useAgentPresets } from "@/hooks/use-agent-presets"
import { useUpdateChat } from "@/hooks/use-chat"
import { isAgentToolSelectable } from "@/lib/agent-tools"
import { useUserScopes } from "@/lib/hooks"
import { useQuery } from "@/lib/query"
import { hasGrantedScope } from "@/lib/scopes"
import {
  resolveChatSettings,
  selectChatCapabilities,
} from "@/lib/workspace-chat"

/** Editable per-chat selections, bounded by current workspace limits. */
export function useWorkspaceChatTools({
  workspaceId,
  chat,
  enabled,
  registryActions,
  mcpIntegrations,
}: {
  workspaceId: string
  chat?: AgentSessionReadVercel
  enabled: boolean
  registryActions: RegistryActionReadMinimal[]
  mcpIntegrations: MCPIntegrationRead[]
}) {
  const [draft, setDraft] = useState<WorkspaceChatOverrides | null>(null)
  // The pane is reused across chats; a new chat must inherit workspace limits.
  const [draftChatId, setDraftChatId] = useState(chat?.id)
  if (draftChatId !== chat?.id) {
    setDraftChatId(chat?.id)
    setDraft(null)
  }
  const {
    data: workspace,
    isLoading,
    error,
  } = useQuery({
    queryKey: ["workspace", workspaceId],
    queryFn: () => workspacesGetWorkspace({ workspaceId }),
    enabled,
  })
  const {
    userScopes,
    isLoading: scopesLoading,
    error: scopesError,
  } = useUserScopes(workspaceId, { enabled })
  const { updateChat, isUpdating } = useUpdateChat(workspaceId)
  const {
    presets = [],
    presetsIsLoading,
    presetsError,
  } = useAgentPresets(workspaceId, { enabled })
  const limits = resolveChatSettings(workspace?.settings?.chat)
  const overrides = chat ? (chat.workspace_chat_overrides ?? null) : draft
  const scopes = new Set(userScopes?.scopes ?? [])
  const allowedActions = new Set(
    selectChatCapabilities(
      registryActions
        .filter(
          (action) =>
            isAgentToolSelectable(action.action) &&
            hasGrantedScope(`action:${action.action}:execute`, scopes)
        )
        .map((action) => action.action),
      limits.tools
    )
  )
  const allowedMcp = new Set(
    selectChatCapabilities(
      mcpIntegrations
        .filter((server) => server.state === "connected")
        .map((server) => server.id),
      limits.mcp
    )
  )
  const allowedSubagents = new Set(
    selectChatCapabilities(
      presets.map((preset) => preset.id),
      limits.subagents
    )
  )
  const subagents = presets.filter((preset) => allowedSubagents.has(preset.id))

  async function update(overrides: WorkspaceChatOverrides | null) {
    if (!chat) {
      setDraft(overrides)
      return
    }
    try {
      await updateChat({
        chatId: chat.id,
        update: { workspace_chat_overrides: overrides },
      })
    } catch {
      // useUpdateChat rolls back its optimistic cache.
      toast({
        title: "Could not save chat tools",
        description: "Your previous selections have been restored. Try again.",
        variant: "destructive",
      })
    }
  }

  return {
    overrides,
    disabled:
      isLoading ||
      scopesLoading ||
      presetsIsLoading ||
      isUpdating ||
      !!error ||
      !!scopesError ||
      !!presetsError,
    subagents,
    registryActions: registryActions.filter((action) =>
      allowedActions.has(action.action)
    ),
    mcpIntegrations: mcpIntegrations.filter((server) =>
      allowedMcp.has(server.id)
    ),
    selectedTools: selectChatCapabilities(
      [...allowedActions],
      limits.tools,
      overrides?.tools
    ),
    selectedMcpIntegrations: selectChatCapabilities(
      [...allowedMcp],
      limits.mcp,
      overrides?.mcp_integrations
    ),
    selectedSubagents: selectChatCapabilities(
      subagents
        .filter(
          (preset) =>
            preset.current_version_subagent_eligibility?.eligible !== false
        )
        .map((preset) => preset.id),
      limits.subagents,
      overrides?.subagents
    ),
    onToolsChange: (tools: string[]) => void update({ ...overrides, tools }),
    onMcpChange: (mcp_integrations: string[]) =>
      void update({ ...overrides, mcp_integrations }),
    onSubagentsChange: (subagents: string[]) =>
      void update({ ...overrides, subagents }),
    onReset: () => void update(null),
  }
}
