/** URI prefix used only while a workflow command is visible in TipTap. */
export const WORKFLOW_MENTION_URI_SCHEME = "workflow-mention://"

/** Existing agent-mention URI prefix persisted in comment Markdown. */
export const AGENT_MENTION_URI_SCHEME = "mention://agent/"

/** Build the link target persisted for an agent mention. */
export function buildAgentMentionHref(targetId: string): string {
  return `${AGENT_MENTION_URI_SCHEME}${targetId}`
}

/** Build the transient link target used for a selected workflow command. */
export function buildWorkflowMentionHref(targetId: string): string {
  return `${WORKFLOW_MENTION_URI_SCHEME}${targetId}`
}

/** Return whether a link belongs to either comment mention kind. */
export function isCommentMentionHref(href: string | null | undefined): boolean {
  return Boolean(
    href?.startsWith(AGENT_MENTION_URI_SCHEME) ||
      href?.startsWith(WORKFLOW_MENTION_URI_SCHEME)
  )
}

/** Content and workflow metadata ready for the existing comments API. */
export interface SerializedTiptapComment {
  content: string
  workflowId: string | null
}

// Labels may contain escaped Markdown characters. Match escaped characters as
// one unit so an escaped `]` does not terminate the link text early.
const WORKFLOW_MENTION_PATTERN = new RegExp(
  `\\[(?:\\\\.|[^\\]])*\\]\\(${WORKFLOW_MENTION_URI_SCHEME}([^\\s)]+)\\)[ \\t]?`,
  "g"
)

/**
 * Remove TipTap-only workflow links and return the selected workflow id.
 *
 * Agent mention links intentionally remain byte-for-byte Markdown links; the
 * backend already parses their `mention://agent/...` targets. Workflows use a
 * separate request field, so their editor marker must never reach storage.
 */
export function serializeTiptapComment(
  markdown: string
): SerializedTiptapComment {
  let workflowId: string | null = null
  const content = markdown.replace(
    WORKFLOW_MENTION_PATTERN,
    (_match, targetId: string) => {
      workflowId ??= targetId
      return ""
    }
  )
  return { content, workflowId }
}
