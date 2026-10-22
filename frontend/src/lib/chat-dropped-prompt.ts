import type { UIMessage } from "ai"
import type { AgentSessionsGetSessionVercelResponse } from "@/client"
import { getBaseUrl } from "@/lib/api"
import { isUserPrompt, toUIMessage } from "@/lib/chat"
import type { QueryClient } from "@/lib/query"

function storageKey(chatId: string): string {
  return `tracecat:chat:dropped-prompt:${chatId}`
}

/** The reconnect endpoint for a chat's live turn. */
export function chatStreamUrl(chatId: string, workspaceId: string): string {
  const url = new URL(`/api/agent/sessions/${chatId}/stream`, getBaseUrl())
  url.searchParams.set("workspace_id", workspaceId)
  return url.toString()
}

function promptText(message: UIMessage): string {
  return message.parts
    .map((part) => (part.type === "text" ? part.text : ""))
    .join("")
}

function isStoredPrompt(value: unknown): value is UIMessage {
  return (
    typeof value === "object" &&
    value !== null &&
    "role" in value &&
    "parts" in value &&
    Array.isArray(value.parts) &&
    isUserPrompt(value as UIMessage)
  )
}

/** Refetches a chat's server transcript and returns its messages. */
export async function refetchChatTranscript(
  queryClient: QueryClient,
  chatId: string,
  workspaceId: string
): Promise<UIMessage[]> {
  const queryKey = ["chat", chatId, workspaceId, "vercel"]
  // A stale cache never holds a mid-turn prompt, so a failed refetch must not
  // read as the server missing it.
  await queryClient.refetchQueries(
    { queryKey, exact: true },
    { throwOnError: true }
  )
  const session =
    queryClient.getQueryData<AgentSessionsGetSessionVercelResponse>(queryKey)
  return (session?.messages ?? []).map(toUIMessage)
}

/**
 * Whether the server transcript's latest prompt is `prompt`. Server ids differ
 * from client ids, so this matches on text: a repeated prompt reads as held.
 */
function holdsPrompt(serverMessages: UIMessage[], prompt: UIMessage): boolean {
  const lastPrompt = serverMessages.findLast(isUserPrompt)
  return (
    lastPrompt !== undefined && promptText(lastPrompt) === promptText(prompt)
  )
}

/**
 * The prompt a dropped connection cut off, unless the server holds it. A held
 * prompt is forgotten, so a later prompt cannot make it read as missing.
 */
export function readDroppedPrompt(
  chatId: string | undefined,
  serverMessages: UIMessage[]
): UIMessage | null {
  if (!chatId) return null
  try {
    const stored = window.localStorage.getItem(storageKey(chatId))
    const message: unknown = stored ? JSON.parse(stored) : null
    if (!isStoredPrompt(message)) return null
    if (holdsPrompt(serverMessages, message)) {
      window.localStorage.removeItem(storageKey(chatId))
      return null
    }
    return message
  } catch {
    return null
  }
}

/**
 * Keeps the latest prompt of a dropped turn in this browser, so a reload can
 * show it again. Pass null to forget it.
 */
export function saveDroppedPrompt(
  chatId: string,
  turnMessages: UIMessage[] | null
): void {
  // A dropped approval continuation is not a prompt to show again.
  const prompt = turnMessages?.findLast((m) => m.role === "user")
  try {
    if (prompt && isUserPrompt(prompt)) {
      window.localStorage.setItem(storageKey(chatId), JSON.stringify(prompt))
    } else {
      window.localStorage.removeItem(storageKey(chatId))
    }
  } catch {
    // Blocked storage only loses the prompt after a reload.
  }
}

/**
 * Whether the server took a prompt whose stream dropped: its turn is still
 * attachable (200), or the refetched transcript holds it. Throws when the
 * server cannot be reached, so a caller never resends on a guess.
 */
export async function serverTookPrompt({
  chatId,
  workspaceId,
  prompt,
  refetchTranscript,
}: {
  chatId: string
  workspaceId: string
  prompt: UIMessage
  refetchTranscript: () => Promise<UIMessage[]>
}): Promise<boolean> {
  const response = await fetch(chatStreamUrl(chatId, workspaceId), {
    credentials: "include",
  })
  await response.body?.cancel()
  if (!response.ok) {
    throw new Error(`Turn check failed with ${response.status}`)
  }
  if (response.status !== 204) return true
  if (!holdsPrompt(await refetchTranscript(), prompt)) return false
  // The reconnect may seed from a transcript that has not caught up yet.
  saveDroppedPrompt(chatId, null)
  return true
}
