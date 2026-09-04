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
  formatting: string
}

/** A comment mention link and its current document range. */
export interface CommentMentionLinkRange extends CommentMentionLinkSnapshot {
  from: number
  to: number
}

export type MapCommentMentionPosition = (
  position: number,
  association: -1 | 1
) => number

/** Find all internal mention-link ranges in a ProseMirror document. */
export function findCommentMentionLinkRanges(
  doc: ProseMirrorNode
): CommentMentionLinkRange[] {
  const ranges: CommentMentionLinkRange[] = []
  doc.descendants((node, pos) => {
    if (!node.isText) {
      return
    }
    const href = node.marks
      .find((mark) => mark.type.name === "link")
      ?.attrs.href?.toString()
    if (!isCommentMentionHref(href)) {
      return
    }
    const previous = ranges.at(-1)
    const formatting = `${node.nodeSize}:${JSON.stringify(
      node.marks
        .filter((mark) => mark.type.name !== "link")
        .map((mark) => mark.toJSON())
    )}`
    if (previous && previous.href === href && previous.to === pos) {
      previous.to = pos + node.nodeSize
      previous.text += node.text ?? ""
      previous.formatting += `|${formatting}`
      return
    }
    ranges.push({
      from: pos,
      to: pos + node.nodeSize,
      href,
      text: node.text ?? "",
      formatting,
    })
  })
  return ranges
}

/** Find mention links whose label, formatting, or range shape was edited. */
export function findEditedCommentMentionIndexes(
  previous: CommentMentionLinkRange[],
  current: CommentMentionLinkRange[],
  mapPosition: MapCommentMentionPosition = (position) => position
): number[] {
  const editedIndexes = new Set<number>()
  const claimedCurrentIndexes = new Set<number>()

  for (const oldMention of previous) {
    let mappedFrom = mapPosition(oldMention.from, 1)
    let mappedTo = mapPosition(oldMention.to, -1)
    if (mappedFrom >= mappedTo) {
      mappedFrom = mapPosition(oldMention.from, -1)
      mappedTo = mapPosition(oldMention.to, 1)
    }
    const candidates = current
      .map((mention, index) => ({ mention, index }))
      .filter(
        ({ mention, index }) =>
          !claimedCurrentIndexes.has(index) &&
          mention.href === oldMention.href &&
          mention.from < mappedTo &&
          mention.to > mappedFrom
      )

    for (const { index } of candidates) {
      claimedCurrentIndexes.add(index)
    }
    if (candidates.length !== 1) {
      for (const { index } of candidates) {
        editedIndexes.add(index)
      }
      continue
    }
    const candidate = candidates[0]
    if (
      candidate &&
      (candidate.mention.text !== oldMention.text ||
        candidate.mention.formatting !== oldMention.formatting)
    ) {
      editedIndexes.add(candidate.index)
    }
  }

  return [...editedIndexes].sort((left, right) => left - right)
}

/** Content and workflow metadata ready for the existing comments API. */
export interface SerializedTiptapComment {
  content: string
  workflowId: string | null
}

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
  markdown: string,
  workflowMentions: Array<
    Pick<CommentMentionLinkSnapshot, "href" | "text"> & {
      markdownOffset?: number
    }
  >
): SerializedTiptapComment {
  let workflowId: string | null = null
  const workflowMentionLines = new Set<number>()
  const removalRanges: Array<{ from: number; to: number }> = []
  let searchFrom = 0
  for (const mention of workflowMentions) {
    if (!mention.href.startsWith(WORKFLOW_MENTION_URI_SCHEME)) {
      continue
    }
    const targetId = mention.href.slice(WORKFLOW_MENTION_URI_SCHEME.length)
    const marker = `[${mention.text}](${mention.href})`
    const offset =
      mention.markdownOffset === undefined
        ? markdown.indexOf(marker, searchFrom)
        : markdown.startsWith(marker, mention.markdownOffset)
          ? mention.markdownOffset
          : -1
    if (targetId && offset !== -1) {
      workflowId ??= targetId
      workflowMentionLines.add(markdown.slice(0, offset).split("\n").length - 1)
      let markerEnd = offset + marker.length
      if (markdown[markerEnd] === " " || markdown[markerEnd] === "\t") {
        markerEnd += 1
      }
      removalRanges.push({ from: offset, to: markerEnd })
      searchFrom = markerEnd
    }
  }
  let withoutWorkflowMention = markdown
  for (const range of removalRanges.reverse()) {
    withoutWorkflowMention =
      withoutWorkflowMention.slice(0, range.from) +
      withoutWorkflowMention.slice(range.to)
  }
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
