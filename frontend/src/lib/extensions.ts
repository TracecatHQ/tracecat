/**
 * Build-time extension points for the UI.
 *
 * `src/extensions/index.ts` exports the {@link FrontendExtensions} a build
 * uses. Tracecat's is empty. A build that extends the UI replaces the
 * `src/extensions/` directory before `next build`, and every slot it leaves
 * out keeps Tracecat's behavior.
 */
import type { UIMessage } from "ai"
import type { ReactNode } from "react"
import type {
  AgentControlReceipt,
  AgentPendingMessageRead,
  AgentSessionCapabilities,
} from "@/client"

/** A withdraw of pending messages; `id` is reused when retrying. */
export type WithdrawPendingRequest = {
  id: string
  /** Message IDs to withdraw; omit to withdraw every pending message. */
  targetIds?: string[]
}

/** The chat a composer belongs to. */
export interface RunningComposerContext {
  /** Undefined until the chat exists. */
  chatId?: string
  workspaceId: string
  /** What the chat's backend supports. */
  capabilities: AgentSessionCapabilities
  /** Whether a turn is running. */
  running: boolean
  /** The live transcript, including the data parts the stream sent. */
  messages: UIMessage[]
  /**
   * The user's messages the model has not read yet, in send order. Empty
   * unless `capabilities.listing_pending`, a turn is running and the viewer
   * can write to the chat.
   */
  pending: AgentPendingMessageRead[]
  /** Refetch `pending`, e.g. after a steer was accepted. */
  refreshPending: () => Promise<void>
  /**
   * Withdraw pending messages the model has not read; requires
   * `capabilities.withdrawing`. The receipt lists the withdrawn IDs in send
   * order and those already read (`too_late`). Rejects with the API error;
   * after an unknown outcome (503), retry with the same `id`.
   */
  withdraw: (request: WithdrawPendingRequest) => Promise<AgentControlReceipt>
  /** Put text back at the start of the composer, ahead of any draft. */
  restoreInput: (text: string) => void
}

/** Takes over the composer while a turn runs. */
export interface RunningComposer {
  /** Shown in the composer while a turn runs. */
  placeholder: string
  /** Send the composer's text. Resolves false to keep it in the composer. */
  submit: (text: string) => Promise<boolean>
  /** Rendered above the composer, whether or not a turn runs. */
  panel?: ReactNode
  /** Called when Escape is pressed in the composer while a turn runs. */
  onEscape?: () => void
}

export interface FrontendExtensions {
  /**
   * A hook every chat pane calls on each render. Return a composer to accept
   * input while a turn runs, or undefined to keep the composer disabled.
   */
  useRunningComposer?: (
    context: RunningComposerContext
  ) => RunningComposer | undefined
}
