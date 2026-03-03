import { getStreamdownRehypePlugins } from "@/lib/sanitize-markdown"

type HastNode = {
  type: string
  children?: HastNode[]
  properties?: Record<string, unknown>
}

describe("getStreamdownRehypePlugins", () => {
  it("strips inline styles outside KaTeX subtrees", () => {
    const [styleStripPlugin] = getStreamdownRehypePlugins()
    const transform = (styleStripPlugin as () => (tree: HastNode) => void)()

    const tree: HastNode = {
      type: "root",
      children: [
        {
          type: "element",
          properties: {
            className: ["plain-span"],
            style: "position:fixed;top:0;",
          },
          children: [],
        },
        {
          type: "element",
          properties: { className: ["katex"] },
          children: [
            {
              type: "element",
              properties: {
                className: ["katex-html"],
                style: "height:1.3648em;vertical-align:-0.3558em;",
              },
              children: [],
            },
          ],
        },
      ],
    }

    transform(tree)

    expect(tree.children?.[0].properties?.style).toBeUndefined()
    expect(tree.children?.[1].children?.[0].properties?.style).toBe(
      "height:1.3648em;vertical-align:-0.3558em;"
    )
  })
})
