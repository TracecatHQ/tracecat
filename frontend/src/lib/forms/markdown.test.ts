import {
  type FormMarkdownNode,
  formMarkdownToPlainText,
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

describe("remarkFormMarkdown", () => {
  it("drops raw HTML and images and turns unsafe links into text", () => {
    const safe = [
      link("https://example.com/docs", "docs"),
      link("  HTTP://EXAMPLE.COM", "upper"),
      link("mailto:it@example.com", "mail"),
    ]
    const unsafe = [
      "javascript:alert(1)",
      " JaVaScRiPt:alert(1)",
      "data:text/html,x",
      "vbscript:msgbox(1)",
      "file:///etc/passwd",
      "//example.com",
      "/workspaces",
      "#anchor",
    ]
    const tree: FormMarkdownNode = {
      type: "root",
      children: [
        { type: "html", value: "<div onclick='x()'>block</div>" },
        { type: "definition", identifier: "ref", url: "https://example.com" },
        paragraph(
          text("Hello "),
          { type: "html", value: "<script>" },
          { type: "image", url: "https://example.com/x.png", alt: "x" },
          { type: "imageReference", identifier: "ref" },
          ...unsafe.map((url) => link(url, url)),
          // Reference links resolve against definitions, which are dropped.
          { type: "linkReference", identifier: "ref", children: [text("ref")] },
          {
            type: "strong",
            children: [
              { type: "html", value: "<img src=x onerror=alert(1)>" },
              link("data:text/html,x", "nested"),
            ],
          },
          ...safe
        ),
      ],
    }

    remarkFormMarkdown()(tree)

    expect(tree.children).toEqual([
      paragraph(
        text("Hello "),
        ...unsafe.map(text),
        text("ref"),
        { type: "strong", children: [text("nested")] },
        ...safe
      ),
    ])
  })
})

describe("formMarkdownToPlainText", () => {
  it.each([
    ["Request a new laptop from IT.", "Request a new laptop from IT."],
    ["**Bold** and *italic* and `code`", "Bold and italic and code"],
    ["__Bold__ and _italic_ and ~~gone~~", "Bold and italic and gone"],
    ["**_Nested_** markers", "Nested markers"],
    ["See the [policy](https://example.com/policy).", "See the policy."],
    ["![diagram](data:image/png;base64,AAAA) Read this", "Read this"],
    ["# Laptops\n\nPick one.", "Laptops Pick one."],
    ["- one\n- two\n1. three", "one two three"],
    ["> Reviewed within 2 days", "Reviewed within 2 days"],
    ["Hello <b>there</b><br/>", "Hello there"],
    [
      "Docs at <https://example.com/docs>.",
      "Docs at https://example.com/docs.",
    ],
    ["Ask <MAILTO:it@example.com>", "Ask MAILTO:it@example.com"],
    ["```\nmba\n```", "mba"],
    ["Uses needed_by_date and 2 * 3 * 4", "Uses needed_by_date and 2 * 3 * 4"],
    ["  spaced\n\n out  ", "spaced out"],
  ])("turns %j into %j", (markdown, expected) => {
    expect(formMarkdownToPlainText(markdown)).toBe(expected)
  })

  it.each([
    "<<a>script>alert(1)</script>",
    "<scr<b>ipt>alert(1)</scr</b>ipt>",
    "<<<i>b>script>alert(1)",
    "<<https://example.com>script>alert(1)",
  ])("leaves no tag behind when tags are nested in %j", (markdown) => {
    const text = formMarkdownToPlainText(markdown)

    expect(text).not.toContain("<script")
    expect(text).not.toMatch(/<\/?[A-Za-z][^>]*>/)
  })
})
