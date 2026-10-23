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
import type { AgentSessionCapabilities } from "@/client"

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
}

/** Takes over the composer while a turn runs. */
export interface RunningComposer {
  /** Shown in the composer while a turn runs. */
  placeholder: string
  /** Send the composer's text. Resolves false to keep it in the composer. */
  submit: (text: string) => Promise<boolean>
  /** Rendered above the composer, whether or not a turn runs. */
  panel?: ReactNode
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
