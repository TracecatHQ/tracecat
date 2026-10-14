/** The subset of an mdast node the form markdown filter reads. */
export interface FormMarkdownNode {
  type: string
  url?: string
  children?: FormMarkdownNode[]
  [key: string]: unknown
}

const SAFE_LINK_PATTERN = /^(https?:\/\/|mailto:)/i

/** Whether a link target may be rendered: only `http(s)` and `mailto`. */
export function isSafeFormLink(href: unknown): href is string {
  return typeof href === "string" && SAFE_LINK_PATTERN.test(href.trim())
}

// `html` is raw HTML, block or inline. `definition` only feeds reference
// links and images, which are not supported.
const DROPPED_NODE_TYPES = new Set([
  "html",
  "image",
  "imageReference",
  "definition",
])

function filterNodes(nodes: FormMarkdownNode[]): FormMarkdownNode[] {
  return nodes.flatMap((node) => {
    if (DROPPED_NODE_TYPES.has(node.type)) {
      return []
    }
    const children = node.children && filterNodes(node.children)
    if (
      node.type === "linkReference" ||
      (node.type === "link" && !isSafeFormLink(node.url))
    ) {
      return children ?? []
    }
    return [children ? { ...node, children } : node]
  })
}

/**
 * Remark plugin that reduces a document to what a form may show.
 *
 * Raw HTML and images are removed. A link that is not `http(s)` or `mailto`,
 * and any reference-style link, is replaced by its own text. It runs on the
 * markdown tree, before any HTML exists, so a dropped `html` node never
 * reaches the raw-HTML parser further down the pipeline.
 */
export function remarkFormMarkdown() {
  return (tree: FormMarkdownNode): void => {
    tree.children = filterNodes(tree.children ?? [])
  }
}

/**
 * Reduce author markdown to one line of plain text, for list rows that have
 * no room for formatting.
 *
 * This is a display helper, not a sanitizer: the result is only ever rendered
 * as text. Images and raw HTML tags are dropped, links keep their text and
 * block and inline markers are removed. Tags are removed until none is left,
 * so a tag split around another one (`<<a>script>`) does not survive.
 */
export function formMarkdownToPlainText(markdown: string): string {
  const text = stripHtmlTags(markdown.replace(/^\s*(```|~~~).*$/gm, ""))
    .replace(/!\[[^\]]*\]\([^)]*\)/g, "")
    .replace(/\[([^\]]*)\]\([^)]*\)/g, "$1")
    .replace(/^\s*(#{1,6}|>|[-*+]|\d+[.)])\s+/gm, "")
  return stripInlineMarkers(stripInlineMarkers(text))
    .replace(/\s+/g, " ")
    .trim()
}

// Removing a tag can join the text around it into a new tag, so repeat until
// the text stops changing.
function stripHtmlTags(text: string): string {
  let previous: string
  let current = text
  do {
    previous = current
    current = current.replace(/<\/?[A-Za-z][^>]*>/g, "")
  } while (current !== previous)
  return current
}

// Run twice by the caller so nested markers such as `**_text_**` both go.
function stripInlineMarkers(text: string): string {
  return (
    text
      // A marker has to touch its text: `2 * 3 * 4` is arithmetic.
      .replace(/(\*\*|\*|~~|`)(\S|\S.*?\S)\1/g, "$2")
      // Underscores only count at a word edge: `needed_by_date` is a name.
      .replace(/(^|[^A-Za-z0-9])(__|_)(\S|\S.*?\S)\2(?![A-Za-z0-9])/g, "$1$3")
  )
}
