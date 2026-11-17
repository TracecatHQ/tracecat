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
import {
  CANCELLED_DATA_PART_TYPE,
  toUIMessage,
  transformMessages,
} from "@/lib/chat"
import type { SubagentStreamStore } from "@/lib/subagent-stream"
import { useOptionalWorkspaceId } from "@/providers/workspace-id"

const RECOVERY_TIMEOUT_MS = 60_000

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
  // Child sessions checkpoint their history when they settle, after the
  // parent's `subagent` result may already have arrived. A prompt alone (or an
  // empty response) belongs to a child whose history is not saved yet. A child
  // stopped before replying saves only its cancelled marker.
  return Boolean(
    chat?.messages?.some(
      (message) =>
        message.role === "assistant" ||
        message.parts.some((part) => part.type === CANCELLED_DATA_PART_TYPE)
    ) ||
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
 * Keeps live text visible until the child's saved history loads. A finished
 * child's history can be saved after the parent's result arrives, and the
 * parent connection can stop early, so both poll for the settled child's
 * history without treating the last live snapshot as a live stream.
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
  isRecoveryPaused: boolean
  retryRecovery: () => void
} {
  const context = useContext(SubagentStreamContext)
  const fallbackWorkspaceId = useOptionalWorkspaceId()
  const workspaceId = context?.workspaceId ?? fallbackWorkspaceId
  const liveMessage = useSubagentLiveMessage(sessionId)
  const [adoptedSessionId, setAdoptedSessionId] = useState<string | null>(null)
  const [recoveryPaused, setRecoveryPaused] = useState(false)
  const [recoveryAttempt, setRecoveryAttempt] = useState(0)
  const parentStreaming =
    context?.status === "streaming" || context?.status === "submitted"
  const recovering =
    adoptedSessionId !== sessionId && (finished || !parentStreaming)
  useEffect(() => {
    setRecoveryPaused(false)
    if (!recovering || !sessionId) return
    const timeout = setTimeout(
      () => setRecoveryPaused(true),
      RECOVERY_TIMEOUT_MS
    )
    return () => clearTimeout(timeout)
  }, [recovering, sessionId, recoveryAttempt])
  const retryRecovery = useCallback(() => {
    setRecoveryAttempt((attempt) => attempt + 1)
  }, [])
  const persistedSessionId =
    (recovering || adoptedSessionId === sessionId) && sessionId && workspaceId
      ? sessionId
      : undefined
  const { chat, chatLoading, chatFetching, chatError } = useGetChatVercel({
    chatId: persistedSessionId,
    workspaceId: workspaceId ?? "",
    ...(recovering
      ? { refetchInterval: 3_000, enabled: !recoveryPaused, retry: false }
      : {}),
  })
  useEffect(() => {
    if (
      recovering &&
      !chatFetching &&
      (chatError?.status === 403 || chatError?.status === 404)
    ) {
      setRecoveryPaused(true)
    }
  }, [recovering, chatFetching, chatError])
  const hasPersistedHistory =
    persistedSessionId !== undefined &&
    chat !== undefined &&
    !chatFetching &&
    !chatError &&
    hasSavedChildTranscript(chat)
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
    // A finished child's card keeps loading until its history is saved.
    isLoading:
      persistedSessionId !== undefined &&
      (chatLoading || (finished && recovering && !recoveryPaused)),
    isError: persistedSessionId !== undefined && chatError != null,
    isRecoveryPaused: recovering && recoveryPaused,
    retryRecovery,
  }
}
