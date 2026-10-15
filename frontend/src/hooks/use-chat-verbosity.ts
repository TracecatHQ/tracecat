"use client"

import { createContext, useContext } from "react"
import { type WorkspaceSettingsRead, workspacesGetWorkspace } from "@/client"
import { useQuery } from "@/lib/query"

/** How much of an agent's work a chat draws. Display only. */
export type ChatVerbosity = NonNullable<WorkspaceSettingsRead["chat_verbosity"]>

export const DEFAULT_CHAT_VERBOSITY: ChatVerbosity = "milestones"

/** The verbosity a chat surface draws its messages at. */
export const ChatVerbosityContext = createContext<ChatVerbosity>(
  DEFAULT_CHAT_VERBOSITY
)

/** The verbosity of the surrounding chat surface. */
export function useChatVerbosity(): ChatVerbosity {
  return useContext(ChatVerbosityContext)
}

/** The workspace's chat verbosity setting, defaulting to milestones. */
export function useWorkspaceChatVerbosity(workspaceId: string): ChatVerbosity {
  const { data: workspace } = useQuery({
    queryKey: ["workspace", workspaceId],
    queryFn: () => workspacesGetWorkspace({ workspaceId }),
    enabled: Boolean(workspaceId),
    staleTime: 5 * 60 * 1000,
    refetchOnWindowFocus: false,
  })
  return workspace?.settings?.chat_verbosity ?? DEFAULT_CHAT_VERBOSITY
}
