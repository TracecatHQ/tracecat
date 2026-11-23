import {
  type ArtifactMentionType,
  isReferenceURI,
  parseReferenceURI,
  ReferenceURIError,
  serializeReferenceURI,
} from "@/lib/agent-reference-uri"

/** A case an agent mentioned in its reply. */
export interface ArtifactMention {
  type: ArtifactMentionType
  id: string
}

const SCHEME = "tracecat-ref://"
// The sanitizer drops the reserved scheme, so mentions travel as this path.
const MENTION_PATH_PREFIX = "/tracecat-ref/"

/** Parse a case mention URI; anything else is not a mention. */
export function parseArtifactMention(uri: string): ArtifactMention | null {
  try {
    const target = parseReferenceURI(uri)
    return target.kind === "artifact" && target.artifactType
      ? { type: target.artifactType, id: target.identity }
      : null
  } catch (error) {
    if (error instanceof ReferenceURIError) return null
    throw error
  }
}

/** Read back a mention the remark plugin rewrote into a link path. */
export function mentionFromHref(href: unknown): ArtifactMention | null {
  return typeof href === "string" && href.startsWith(MENTION_PATH_PREFIX)
    ? parseArtifactMention(SCHEME + href.slice(MENTION_PATH_PREFIX.length))
    : null
}

interface MarkdownNode {
  type: string
  url?: string
  children?: MarkdownNode[]
  [key: string]: unknown
}

function rewriteLinks(parent: MarkdownNode): void {
  if (!parent.children) return
  parent.children = parent.children.flatMap((node) => {
    rewriteLinks(node)
    if (node.type !== "link" || !node.url || !isReferenceURI(node.url)) {
      return [node]
    }
    const mention = parseArtifactMention(node.url)
    if (!mention) return node.children ?? []
    const canonical = serializeReferenceURI({
      kind: "artifact",
      identity: mention.id,
      artifactType: mention.type,
    })
    node.url = MENTION_PATH_PREFIX + canonical.slice(SCHEME.length)
    return [node]
  })
}

/**
 * Remark plugin for agent replies. A case mention becomes a link the
 * mention-aware `a` component recognizes; any other reserved link becomes
 * its own text, never a broken or blocked link.
 */
export function remarkArtifactMentions() {
  return (tree: MarkdownNode): void => rewriteLinks(tree)
}
