import type { UIMessage } from "ai"
import {
  createContext,
  useCallback,
  useContext,
  useMemo,
  useSyncExternalStore,
} from "react"
import { useGetChatVercel } from "@/hooks/use-chat"
import { toUIMessage, transformMessages } from "@/lib/chat"
import type { SubagentStreamStore } from "@/lib/subagent-stream"
import { useOptionalWorkspaceId } from "@/providers/workspace-id"

/** Live child-session transcripts for the chat that renders subagent cards. */
export type SubagentStreamContextValue = {
  store: SubagentStreamStore
  workspaceId: string
}

/**
 * Provided by chat panes with a live stream. Without it, subagent cards fall
 * back to the child's persisted transcript.
 */
export const SubagentStreamContext =
  createContext<SubagentStreamContextValue | null>(null)

function noop() {}

/** Subscribe to the live transcript of a child session, if one is streaming. */
export function useSubagentLiveMessage(
  sessionId: string | null
): UIMessage | undefined {
  const store = useContext(SubagentStreamContext)?.store
  const subscribe = useCallback(
    (listener: () => void) =>
      store && sessionId ? store.subscribe(sessionId, listener) : noop,
    [store, sessionId]
  )
  const getSnapshot = useCallback(
    () => (store && sessionId ? store.getMessage(sessionId) : undefined),
    [store, sessionId]
  )
  return useSyncExternalStore(subscribe, getSnapshot, getSnapshot)
}

/**
 * Transcript for a subagent card.
 *
 * Uses the live child stream when this page received it. Otherwise, once the
 * child has finished, loads its persisted history (e.g. after a refresh).
 */
export function useSubagentTranscript({
  sessionId,
  finished,
}: {
  sessionId: string | null
  finished: boolean
}): { messages: UIMessage[]; isLive: boolean; isLoading: boolean } {
  const context = useContext(SubagentStreamContext)
  const fallbackWorkspaceId = useOptionalWorkspaceId()
  const workspaceId = context?.workspaceId ?? fallbackWorkspaceId
  const liveMessage = useSubagentLiveMessage(sessionId)
  const persistedSessionId =
    finished && !liveMessage && sessionId && workspaceId ? sessionId : undefined
  const { chat, chatLoading } = useGetChatVercel({
    chatId: persistedSessionId,
    workspaceId: workspaceId ?? "",
  })

  const messages = useMemo(() => {
    if (liveMessage) {
      return transformMessages([liveMessage])
    }
    if (!persistedSessionId || !chat) {
      return []
    }
    // The task prompt is shown on the card itself.
    return transformMessages((chat.messages ?? []).map(toUIMessage)).filter(
      (message) => message.role !== "user"
    )
  }, [chat, liveMessage, persistedSessionId])

  return {
    messages,
    isLive: liveMessage !== undefined,
    isLoading: persistedSessionId !== undefined && chatLoading,
  }
}
