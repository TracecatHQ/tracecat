import {
  type FormMarkdownNode,
  filterFormMarkdownNodes,
  isSafeFormLink,
  remarkFormMarkdown,
} from "@/lib/forms/markdown"

const text = (value: string): FormMarkdownNode => ({ type: "text", value })
const paragraph = (...children: FormMarkdownNode[]): FormMarkdownNode => ({
  type: "paragraph",
  children,
})
const link = (url: string, label: string): FormMarkdownNode => ({
  type: "link",
  url,
  children: [text(label)],
})

describe("isSafeFormLink", () => {
  it.each([
    "https://example.com/docs",
    "http://example.com",
    "HTTPS://EXAMPLE.COM",
    "mailto:it@example.com",
    "  https://example.com  ",
  ])("allows %s", (href) => {
    expect(isSafeFormLink(href)).toBe(true)
  })

  it.each([
    "javascript:alert(1)",
    "JaVaScRiPt:alert(1)",
    " javascript:alert(1)",
    "data:text/html,<script>alert(1)</script>",
    "vbscript:msgbox(1)",
    "file:///etc/passwd",
    "tel:+15555550100",
    "//example.com",
    "/workspaces",
    "#anchor",
    "",
    undefined,
    null,
  ])("blocks %p", (href) => {
    expect(isSafeFormLink(href)).toBe(false)
  })
})

describe("filterFormMarkdownNodes", () => {
  it("drops raw HTML, block and inline", () => {
    expect(
      filterFormMarkdownNodes([
        { type: "html", value: "<div onclick='x()'>block</div>" },
        paragraph(
          text("Hello "),
          { type: "html", value: "<script>" },
          text("alert(1)"),
          { type: "html", value: "</script>" }
        ),
      ])
    ).toEqual([paragraph(text("Hello "), text("alert(1)"))])
  })

  it("drops images, image references and definitions", () => {
    expect(
      filterFormMarkdownNodes([
        paragraph(
          { type: "image", url: "https://example.com/x.png", alt: "x" },
          { type: "imageReference", identifier: "ref" }
        ),
        { type: "definition", identifier: "ref", url: "https://example.com" },
      ])
    ).toEqual([paragraph()])
  })

  it("replaces an unsafe link with its text and keeps a safe one", () => {
    const safe = link("https://example.com/docs", "docs")
    expect(
      filterFormMarkdownNodes([
        paragraph(link("javascript:alert(1)", "click"), safe),
      ])
    ).toEqual([paragraph(text("click"), safe)])
  })

  it("replaces reference links with their text", () => {
    expect(
      filterFormMarkdownNodes([
        paragraph({
          type: "linkReference",
          identifier: "ref",
          children: [text("label")],
        }),
      ])
    ).toEqual([paragraph(text("label"))])
  })

  it("filters inside nested nodes", () => {
    const list: FormMarkdownNode = {
      type: "list",
      children: [
        {
          type: "listItem",
          children: [
            paragraph({
              type: "strong",
              children: [
                { type: "html", value: "<img src=x onerror=alert(1)>" },
                link("data:text/html,x", "nested"),
              ],
            }),
          ],
        },
      ],
    }
    expect(filterFormMarkdownNodes([list])).toEqual([
      {
        type: "list",
        children: [
          {
            type: "listItem",
            children: [
              paragraph({ type: "strong", children: [text("nested")] }),
            ],
          },
        ],
      },
    ])
  })
})

describe("remarkFormMarkdown", () => {
  it("filters the document in place", () => {
    const tree: FormMarkdownNode = {
      type: "root",
      children: [{ type: "html", value: "<b>x</b>" }, paragraph(text("ok"))],
    }
    remarkFormMarkdown()(tree)
    expect(tree.children).toEqual([paragraph(text("ok"))])
  })
})
