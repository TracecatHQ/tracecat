import type { ChatStatus, UIMessage } from "ai"
import {
  createContext,
  useCallback,
  useContext,
  useEffect,
  useMemo,
  useState,
  useSyncExternalStore,
} from "react"
import type { AgentSessionsGetSessionVercelResponse } from "@/client"
import { useGetChatVercel } from "@/hooks/use-chat"
import { toUIMessage, transformMessages } from "@/lib/chat"
import type { SubagentStreamStore } from "@/lib/subagent-stream"
import { useOptionalWorkspaceId } from "@/providers/workspace-id"

/** Live child-session transcripts for the chat that renders subagent cards. */
export type SubagentStreamContextValue = {
  store: SubagentStreamStore
  workspaceId: string
  status: ChatStatus
}

/**
 * Provided by chat panes with a live stream. Without it, subagent cards fall
 * back to the child's persisted transcript.
 */
export const SubagentStreamContext =
  createContext<SubagentStreamContextValue | null>(null)

function noop() {}

function hasSavedChildTranscript(
  chat: AgentSessionsGetSessionVercelResponse | undefined
): boolean {
  // Child sessions checkpoint their history when they settle. A prompt alone
  // (or an empty response) can still belong to a running child.
  return Boolean(
    chat?.messages?.some((message) => message.role === "assistant") ||
      (chat && "last_error" in chat && chat.last_error)
  )
}

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
 * Keeps live text visible until a completed child's persisted history loads.
 * If the parent connection stops early, poll for the settled child's history
 * without treating the interrupted snapshot as a live stream.
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
  const [adoptedSessionId, setAdoptedSessionId] = useState<string | null>(null)
  const parentStreaming =
    context?.status === "streaming" || context?.status === "submitted"
  const recovering =
    !finished && !parentStreaming && adoptedSessionId !== sessionId
  const persistedSessionId =
    (finished || recovering || adoptedSessionId === sessionId) &&
    sessionId &&
    workspaceId
      ? sessionId
      : undefined
  const { chat, chatLoading, chatFetching, chatError } = useGetChatVercel({
    chatId: persistedSessionId,
    workspaceId: workspaceId ?? "",
    ...(recovering ? { refetchInterval: 3_000 } : {}),
  })
  const hasPersistedHistory =
    persistedSessionId !== undefined &&
    chat !== undefined &&
    !chatFetching &&
    !chatError &&
    (finished || hasSavedChildTranscript(chat))
  useEffect(() => {
    if (hasPersistedHistory && persistedSessionId) {
      setAdoptedSessionId(persistedSessionId)
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
    isLive:
      parentStreaming && !hasPersistedHistory && liveMessage !== undefined,
    isLoading: persistedSessionId !== undefined && chatLoading,
    isError: persistedSessionId !== undefined && chatError != null,
  }
}
