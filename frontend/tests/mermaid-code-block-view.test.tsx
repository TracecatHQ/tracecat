import { act, render } from "@testing-library/react"
import { EditorContent, useEditor } from "@tiptap/react"
import StarterKit from "@tiptap/starter-kit"
import { MermaidCodeBlock } from "@/components/tiptap-node/mermaid-code-block-node/mermaid-code-block-node"

jest.mock("mermaid", () => ({
  __esModule: true,
  default: {
    initialize: jest.fn(),
    render: jest.fn(async () => ({ svg: "<svg></svg>" })),
  },
}))

const MERMAID_DOC = {
  type: "doc",
  content: [
    {
      type: "codeBlock",
      attrs: { language: "mermaid" },
      content: [{ type: "text", text: "graph TD\n  a --> b" }],
    },
  ],
}

function ReadOnlyEditor() {
  const editor = useEditor({
    editable: false,
    immediatelyRender: true,
    extensions: [StarterKit.configure({ codeBlock: false }), MermaidCodeBlock],
    content: MERMAID_DOC,
  })

  return <EditorContent editor={editor} />
}

describe("MermaidCodeBlock node view", () => {
  it("hides the Mermaid source when rendering the diagram read-only", async () => {
    const { container } = render(<ReadOnlyEditor />)
    await act(async () => {
      await Promise.resolve()
    })

    const wrapper = container.querySelector('[data-mermaid-code-block="true"]')
    expect(wrapper).not.toBeNull()

    const source = container.querySelector("[data-node-view-content-react]")
    expect(source?.textContent).toContain("graph TD")
    expect(source?.closest(".hidden")).not.toBeNull()
  })
})
