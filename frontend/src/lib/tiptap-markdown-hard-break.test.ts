import { MarkdownHardBreak } from "@/lib/tiptap-markdown-hard-break"

describe("TipTap Markdown hard breaks", () => {
  it("maps Markdown br tokens to TipTap hardBreak nodes", () => {
    const parseMarkdown = MarkdownHardBreak.config.parseMarkdown
    if (!parseMarkdown) {
      throw new Error("Hard-break Markdown parser is missing")
    }
    const createNode = jest.fn(() => ({ type: "hardBreak" }))
    const token = { type: "br", raw: "  \n" } as unknown as Parameters<
      typeof parseMarkdown
    >[0]
    const helpers = { createNode } as unknown as Parameters<
      typeof parseMarkdown
    >[1]

    expect(MarkdownHardBreak.config.markdownTokenName).toBe("br")
    expect(parseMarkdown(token, helpers)).toEqual({ type: "hardBreak" })
    expect(createNode).toHaveBeenCalledWith("hardBreak")
  })
})
