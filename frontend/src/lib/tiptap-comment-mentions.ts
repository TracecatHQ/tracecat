import type { MarkType, Node as ProseMirrorNode } from "@tiptap/pm/model"

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

/** Prevent internal mention links from triggering native browser navigation. */
export function preventCommentMentionNavigation(event: MouseEvent): boolean {
  const target = event.target
  if (!(target instanceof Element)) {
    return false
  }
  const href = target.closest("a")?.getAttribute("href")
  if (!isCommentMentionHref(href)) {
    return false
  }
  event.preventDefault()
  return true
}

/** Convert hard breaks to whitespace while scanning for a mention token. */
export function commentMentionLeafText(node: ProseMirrorNode): string {
  return node.type.name === "hardBreak" ? "\n" : "\ufffc"
}

/** Return whether a text block can represent a comment mention link. */
export function nodeAllowsCommentMention(
  node: ProseMirrorNode,
  linkMark: MarkType | undefined
): boolean {
  return Boolean(linkMark && node.type.allowsMarkType(linkMark))
}

/** A comment mention's stable identity and visible label. */
export interface CommentMentionLinkSnapshot {
  href: string
  text: string
}

/** Find mention links whose visible label was edited in place. */
export function findEditedCommentMentionIndexes(
  previous: CommentMentionLinkSnapshot[],
  current: CommentMentionLinkSnapshot[]
): number[] {
  if (previous.length !== current.length) {
    return []
  }
  return current.flatMap((mention, index) => {
    const oldMention = previous[index]
    if (oldMention?.href === mention.href && oldMention.text !== mention.text) {
      return [index]
    }
    return []
  })
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

const EMPTY_MARKDOWN_CONTAINER_PATTERN =
  /^\s*(?:>\s*)*(?:(?:#{1,6}|[-+*]|\d+[.)])\s*(?:\[[ xX]\]\s*)?)?[*_~`]*\s*$/

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
  const workflowMentionLines = new Set<number>()
  const withoutWorkflowMention = markdown.replace(
    WORKFLOW_MENTION_PATTERN,
    (_match, targetId: string, offset: number) => {
      workflowId ??= targetId
      workflowMentionLines.add(markdown.slice(0, offset).split("\n").length - 1)
      return ""
    }
  )
  const content = withoutWorkflowMention
    .split("\n")
    .filter(
      (line, index) =>
        !workflowMentionLines.has(index) ||
        !EMPTY_MARKDOWN_CONTAINER_PATTERN.test(line)
    )
    .join("\n")
  return { content, workflowId }
}
