import { MarkdownManager } from "@tiptap/markdown"
import {
  isReferenceURI,
  parseReferenceURI,
  type ReferenceTarget,
  ReferenceURIError,
  type ReferenceURIErrorCode,
} from "@/lib/agent-reference-uri"

interface MarkdownToken {
  type: string
  raw: string
  block?: boolean
  href?: string
  tokens?: MarkdownToken[]
  items?: MarkdownToken[]
}

/** Extract syntax-only references using TipTap's actual Markdown lexer.
 * Server preparation must still resolve and authorize every target.
 */
export function parseMarkdownReferences(markdown: string): {
  references: ReferenceTarget[]
  diagnostics: ReferenceURIErrorCode[]
} {
  const lines = markdown
    .replace(/^\uFEFF/, "")
    .replace(/\r\n?/g, "\n")
    .split("\n")
  if (lines[0]?.trim() === "---") {
    let end = lines.findIndex(
      (line, i) => i > 0 && ["---", "..."].includes(line.trim())
    )
    if (end < 0) end = lines.length - 1
    lines.splice(0, end + 1)
  }
  const manager = new MarkdownManager({
    extensions: [],
    markedOptions: { gfm: false },
  })
  const references: ReferenceTarget[] = []
  const diagnostics: ReferenceURIErrorCode[] = []
  function walk(tokens: MarkdownToken[], htmlTags: string[] = []): void {
    for (const token of tokens) {
      if (["code", "codespan", "image"].includes(token.type)) continue
      if (token.type === "html") {
        if (token.block) continue
        const tag = /^<\/?([A-Za-z][A-Za-z0-9-]*)(?=[\s/>])/
          .exec(token.raw)?.[1]
          ?.toLowerCase()
        if (!tag) continue
        if (token.raw.startsWith("</")) {
          if (htmlTags.at(-1) === tag) htmlTags.pop()
        } else if (
          ![
            "area",
            "base",
            "br",
            "col",
            "embed",
            "hr",
            "img",
            "input",
            "link",
            "meta",
            "param",
            "source",
            "track",
            "wbr",
          ].includes(tag) &&
          !token.raw.endsWith("/>")
        ) {
          htmlTags.push(tag)
        }
        continue
      }
      if (
        !htmlTags.length &&
        token.type === "link" &&
        token.href &&
        isReferenceURI(token.href)
      ) {
        try {
          references.push(parseReferenceURI(token.href))
        } catch (error) {
          if (!(error instanceof ReferenceURIError)) throw error
          diagnostics.push(error.code)
        }
      }
      if (token.tokens) {
        // Inline formatting does not close an HTML span. Block boundaries do.
        const inlineContainer = ["em", "strong", "del", "link"].includes(
          token.type
        )
        walk(token.tokens, inlineContainer ? htmlTags : [])
      }
      if (token.items) walk(token.items)
    }
  }
  walk(manager.instance.lexer(lines.join("\n")))
  return { references, diagnostics }
}
