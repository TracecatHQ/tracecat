import { isToolUIPart } from "ai"
import { createContext } from "react"
import type { groupThread } from "@/components/chat/agent-turn"

/** One jump target in a transcript: a user prompt or the agent turn after it. */
export interface TranscriptOutlineEntry {
  /** Matches the `data-outline-id` of the block in the rendered transcript. */
  id: string
  kind: "prompt" | "turn"
  label: string
  /** Tool calls made in the turn; 0 for prompts. */
  toolCallCount: number
}

type ThreadBlocks = ReturnType<typeof groupThread>

const OUTLINE_LABEL_MAX_LENGTH = 80

function firstLine(text: string): string {
  const line = text.trim().split("\n", 1)[0] ?? ""
  return line.replace(/^#+\s*/, "").slice(0, OUTLINE_LABEL_MAX_LENGTH)
}

/** The `data-outline-id` of a thread block. */
export function getOutlineId(block: ThreadBlocks[number]): string {
  return block.kind === "prompt"
    ? `prompt-${block.message.id}`
    : `turn-${block.startIndex}`
}

/** Summarize a grouped thread into its jump targets. */
export function buildTranscriptOutline(
  blocks: ThreadBlocks
): TranscriptOutlineEntry[] {
  const entries: TranscriptOutlineEntry[] = []
  for (const block of blocks) {
    if (block.kind === "prompt") {
      const text = block.message.parts.find((part) => part.type === "text")
      // Prompts without a text part (a channel message, an attachment)
      // are still rendered, so they still get a jump target.
      const label = text?.type === "text" ? firstLine(text.text) : ""
      entries.push({
        id: getOutlineId(block),
        kind: "prompt",
        label: label || "Prompt",
        toolCallCount: 0,
      })
      continue
    }
    let toolCallCount = 0
    let label = ""
    for (const message of block.messages) {
      for (const part of message.parts) {
        if (isToolUIPart(part)) {
          toolCallCount += 1
        } else if (part.type === "text" && part.text.trim()) {
          // The last text of the turn is its answer.
          label = firstLine(part.text)
        }
      }
    }
    entries.push({
      id: getOutlineId(block),
      kind: "turn",
      label: label || "Agent response",
      toolCallCount,
    })
  }
  return entries
}

/**
 * Lets a surface around a transcript receive its outline. The transcript
 * reports only when a provider is present, so other chats pay nothing.
 */
export const TranscriptOutlineContext = createContext<
  ((entries: TranscriptOutlineEntry[]) => void) | null
>(null)
