import { getStreamdownRehypePlugins } from "@/lib/sanitize-markdown"

type HastNode = {
  type: string
  tagName?: string
  children?: HastNode[]
  properties?: Record<string, unknown>
  position?: {
    start?: unknown
    end?: unknown
  } | null
}

describe("getStreamdownRehypePlugins", () => {
  function getStyleStripTransform() {
    const [styleStripPlugin] = getStreamdownRehypePlugins()
    return (styleStripPlugin as () => (tree: HastNode) => void)()
  }

  it("strips spoofed KaTeX class styles from user HTML nodes", () => {
    const transform = getStyleStripTransform()

    const tree: HastNode = {
      type: "root",
      children: [
        {
          type: "element",
          tagName: "div",
          position: { start: { line: 1 } },
          properties: { className: ["katex"] },
          children: [
            {
              type: "element",
              tagName: "span",
              properties: { style: "position:fixed;inset:0;height:1em;" },
              children: [],
            },
          ],
        },
      ],
    }

    transform(tree)

    expect(tree.children?.[0].children?.[0].properties?.style).toBeUndefined()
  })

  it("keeps allowlisted inline styles in trusted KaTeX trees", () => {
    const transform = getStyleStripTransform()

    const tree: HastNode = {
      type: "root",
      children: [
        {
          type: "element",
          tagName: "span",
          properties: {
            className: ["katex"],
          },
          children: [
            {
              type: "element",
              tagName: "span",
              properties: { className: ["katex-mathml"] },
              children: [],
            },
            {
              type: "element",
              tagName: "span",
              properties: {
                className: ["katex-html"],
                style:
                  "height:1.3648em; vertical-align:-0.3558em; position:relative; top:0em;",
              },
              children: [
                {
                  type: "element",
                  tagName: "svg",
                  properties: { style: "height:1em;top:-0.2em;" },
                  children: [],
                },
              ],
            },
          ],
        },
      ],
    }

    transform(tree)

    expect(tree.children?.[0].children?.[1].properties?.style).toBe(
      "height:1.3648em;vertical-align:-0.3558em;position:relative;top:0em;"
    )
    expect(
      tree.children?.[0].children?.[1].children?.[0].properties?.style
    ).toBe("height:1em;top:-0.2em;")
  })

  it("filters disallowed declarations even inside trusted KaTeX roots", () => {
    const transform = getStyleStripTransform()

    const tree: HastNode = {
      type: "root",
      children: [
        {
          type: "element",
          tagName: "span",
          properties: {
            className: ["katex"],
          },
          children: [
            {
              type: "element",
              tagName: "span",
              properties: { className: ["katex-mathml"] },
              children: [],
            },
            {
              type: "element",
              tagName: "span",
              properties: {
                className: ["katex-html"],
                style: "position:fixed;inset:0;height:1em;",
              },
              children: [],
            },
          ],
        },
        {
          type: "element",
          tagName: "span",
          properties: {
            className: ["katex-error"],
            style: "color:var(--color-muted-foreground);position:fixed;",
          },
          children: [],
        },
      ],
    }

    transform(tree)

    expect(tree.children?.[0].children?.[1].properties?.style).toBe(
      "height:1em;"
    )
    expect(tree.children?.[1].properties?.style).toBe(
      "color:var(--color-muted-foreground);"
    )
  })

  it("preserves non-style heading and link properties", () => {
    const transform = getStyleStripTransform()

    const tree: HastNode = {
      type: "root",
      children: [
        {
          type: "element",
          tagName: "h1",
          properties: { id: "section-title", style: "top:0em;" },
          children: [{ type: "text" }],
        },
        {
          type: "element",
          tagName: "a",
          properties: {
            href: "#section-title",
            title: "Section link",
            style: "color:#fff;",
          },
          children: [{ type: "text" }],
        },
      ],
    }

    transform(tree)

    expect(tree.children?.[0].properties?.id).toBe("section-title")
    expect(tree.children?.[1].properties?.href).toBe("#section-title")
    expect(tree.children?.[1].properties?.title).toBe("Section link")
    expect(tree.children?.[0].properties?.style).toBeUndefined()
    expect(tree.children?.[1].properties?.style).toBeUndefined()
  })
})
