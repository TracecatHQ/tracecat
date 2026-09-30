import type { UIMessage } from "ai"
import {
  createContext,
  useCallback,
  useContext,
  useEffect,
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
 * Keeps live text visible until a completed child's persisted history loads,
 * then releases the streaming snapshot and its reader.
 */
export function useSubagentTranscript({
  sessionId,
  finished,
}: {
  sessionId: string | null
  finished: boolean
}): {
  messages: UIMessage[]
  isLive: boolean
  isLoading: boolean
  isError: boolean
} {
  const context = useContext(SubagentStreamContext)
  const fallbackWorkspaceId = useOptionalWorkspaceId()
  const workspaceId = context?.workspaceId ?? fallbackWorkspaceId
  const liveMessage = useSubagentLiveMessage(sessionId)
  const persistedSessionId =
    finished && sessionId && workspaceId ? sessionId : undefined
  const { chat, chatLoading, chatFetching, chatError } = useGetChatVercel({
    chatId: persistedSessionId,
    workspaceId: workspaceId ?? "",
  })
  const hasPersistedHistory =
    persistedSessionId !== undefined &&
    chat !== undefined &&
    !chatFetching &&
    !chatError
  useEffect(() => {
    if (hasPersistedHistory && persistedSessionId) {
      context?.store.release(persistedSessionId)
    }
  }, [context?.store, hasPersistedHistory, persistedSessionId])

  const messages = useMemo(() => {
    if (!hasPersistedHistory && liveMessage) {
      return transformMessages([liveMessage])
    }
    if (!persistedSessionId || !chat) {
      return []
    }
    // The task prompt is shown on the card itself.
    return transformMessages((chat.messages ?? []).map(toUIMessage)).filter(
      (message) => message.role !== "user"
    )
  }, [chat, liveMessage, hasPersistedHistory, persistedSessionId])

  return {
    messages,
    isLive: !hasPersistedHistory && liveMessage !== undefined,
    isLoading: persistedSessionId !== undefined && chatLoading,
    isError: persistedSessionId !== undefined && chatError != null,
  }
}
