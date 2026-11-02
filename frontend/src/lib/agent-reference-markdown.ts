import { MarkdownManager } from "@tiptap/markdown"
import { decodeHTMLStrict } from "entities"
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

function decodeMarkdownEntity(entity: string): string {
  const numeric = /^&#(x[\da-f]+|\d+);$/i.exec(entity)?.[1]
  if (!numeric) return decodeHTMLStrict(entity)
  const code = numeric.toLowerCase().startsWith("x")
    ? Number.parseInt(numeric.slice(1), 16)
    : Number.parseInt(numeric, 10)
  // Match the pinned MarkdownIt decoder, which keeps invalid numeric entities
  // literal instead of applying HTML's legacy control-code replacements.
  if (
    code > 0x10ffff ||
    (code >= 0xd800 && code <= 0xdfff) ||
    (code >= 0xfdd0 && code <= 0xfdef) ||
    (code & 0xffff) === 0xffff ||
    (code & 0xffff) === 0xfffe ||
    code <= 8 ||
    code === 11 ||
    (code >= 14 && code <= 31) ||
    (code >= 127 && code <= 159)
  )
    return entity
  return String.fromCodePoint(code)
}

function decodeMarkdownDestination(destination: string): string {
  // Decode escapes and entities together: an escaped ampersand stays literal,
  // and an entity that produces another entity is never decoded twice.
  return destination.replace(
    /\\([!"#$%&'()*+,\-./:;<=>?@[\\\]^_`{|}~])|&(?:#\d{1,8}|#[xX][\da-fA-F]{1,8}|[A-Za-z][A-Za-z\d]{1,31});/g,
    (match: string, escaped: string | undefined) =>
      escaped ?? decodeMarkdownEntity(match)
  )
}

/** Extract syntax-only references using TipTap's actual Markdown lexer.
 * Server preparation must still resolve and authorize every target.
 * Source-bearing editor diagnostics will come from the server preview API;
 * this helper compares syntax identities and codes, not editor positions.
 */
export function parseMarkdownReferences(markdown: string): {
  references: ReferenceTarget[]
  diagnostics: ReferenceURIErrorCode[]
} {
  const lines = markdown
    .replace(/^\uFEFF/, "")
    .replace(/\r\n?/g, "\n")
    .split("\n")
  if (/^---[\t ]*$/.test(lines[0] ?? "")) {
    let end = lines.findIndex(
      (line, i) => i > 0 && /^(?:---|\.\.\.)[\t ]*$/.test(line)
    )
    if (end < 0) end = lines.length - 1
    lines.splice(0, end + 1)
  }
  const manager = new MarkdownManager({
    extensions: [],
  })
  const nativeTokenizer = manager.instance.Tokenizer.prototype
  const tokenizer = new manager.instance.Tokenizer()
  tokenizer.html = function html(source) {
    const token = nativeTokenizer.html.call(this, source)
    if (!token || token.raw.endsWith("\n")) return token
    // CommonMark HTML blocks include the entire closing line. Running in
    // the block tokenizer preserves this rule inside lists and blockquotes.
    const lineEnd = source.indexOf("\n", token.raw.length)
    const raw = source.slice(0, lineEnd < 0 ? source.length : lineEnd + 1)
    return { ...token, raw, text: raw }
  }
  tokenizer.link = function link(source) {
    // Only space, tab and LF separate the destination from Markdown syntax.
    // Other whitespace belongs to the URI and must survive strict validation.
    const rule = this.rules.inline.link
    const linkRule = new RegExp(
      rule.source
        .replaceAll("\\s*", "[ \\t\\n]*")
        .replace("[^ \\t\\n\\x00-\\x1f]*", "[^ \\t\\n\\x00-\\x1f\\x7f]*"),
      rule.flags
    )
    const linkTokenizer = new manager.instance.Tokenizer(this.options)
    linkTokenizer.rules = {
      ...this.rules,
      inline: { ...this.rules.inline, link: linkRule },
    }
    linkTokenizer.lexer = this.lexer
    const token = linkTokenizer.link(source)
    if (!token) return token
    // Re-read the destination from the consumed source before Marked's
    // backslash unescaping loses the distinction between & and \\&.
    const match = linkRule.exec(token.raw)
    if (match) {
      const destination = match[2].replace(/^<([\s\S]*)>$/, "$1")
      token.href = decodeMarkdownDestination(destination)
    }
    return token
  }
  tokenizer.def = function def(source) {
    // JavaScript's dot excludes U+2028/U+2029, but CommonMark destinations
    // exclude only actual newlines. Keep the native definition grammar otherwise.
    const rule = this.rules.block.def
    const definitionRule = new RegExp(
      rule.source
        .replace("<.*?>", "<[^\\n]*?>")
        .replaceAll(" *", "[ \\t]*")
        .replaceAll(" +", "[ \\t]+")
        .replace("[^<\\s][^\\s]*", "[^<\\x00-\\x20\\x7f][^\\x00-\\x20\\x7f]*"),
      rule.flags
    )
    const definitionTokenizer = new manager.instance.Tokenizer()
    definitionTokenizer.rules = {
      ...this.rules,
      block: { ...this.rules.block, def: definitionRule },
    }
    const token = definitionTokenizer.def(source)
    if (!token) return token
    const match = definitionRule.exec(token.raw)
    if (match?.[2]) {
      const destination = match[2].replace(/^<([\s\S]*)>$/, "$1")
      token.href = decodeMarkdownDestination(destination)
    }
    return token
  }
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
  walk(manager.instance.lexer(lines.join("\n"), { gfm: false, tokenizer }))
  return { references, diagnostics }
}
