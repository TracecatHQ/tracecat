import { HardBreak } from "@tiptap/extension-hard-break"

const parseMarkdownHardBreak: NonNullable<
  typeof HardBreak.config.parseMarkdown
> = (_token, helpers) => helpers.createNode("hardBreak")

/** A hard-break extension that restores Markdown's `br` token on parse. */
export const MarkdownHardBreak = HardBreak.extend({
  markdownTokenName: "br",
  parseMarkdown: parseMarkdownHardBreak,
})
