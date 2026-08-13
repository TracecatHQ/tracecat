import { TableCell } from "@tiptap/extension-table-cell"
import { TableHeader } from "@tiptap/extension-table-header"
import { TableRow } from "@tiptap/extension-table-row"
import { columnResizingPluginKey } from "@tiptap/pm/tables"
import { Editor } from "@tiptap/react"
import { StarterKit } from "@tiptap/starter-kit"
import { materializeColumnWidthsPluginKey } from "@/components/tiptap-node/table-node/table-materialize-widths"
import { TracecatTable } from "@/components/tiptap-node/table-node/table-node-extension"

function createEditor(): { editor: Editor; element: HTMLElement } {
  const element = document.createElement("div")
  document.body.appendChild(element)
  const editor = new Editor({
    element,
    extensions: [
      StarterKit,
      TracecatTable.configure({
        resizable: true,
        cellMinWidth: 48,
        handleWidth: 6,
      }),
      TableRow,
      TableHeader,
      TableCell,
    ],
    content: "<p>before</p>",
  })
  return { editor, element }
}

describe("table resize wiring", () => {
  it("installs the materialize plugin exactly once", () => {
    // `Extension.extend()` copies the parent's whole config onto the child and
    // also chains `parent`, so `this.parent?.()` re-enters the same
    // `addProseMirrorPlugins` body. Layering a second extend over the table
    // therefore mints a second plugin against the same key, and ProseMirror
    // rejects the editor outright. Everything must stay in one `Table.extend`.
    // Constructing the editor is itself the assertion: ProseMirror rejects a
    // second instance of a keyed plugin, so a duplicate throws here rather
    // than reaching the count below.
    const { editor, element } = createEditor()
    try {
      expect(materializeColumnWidthsPluginKey.get(editor.state)).toBeDefined()
    } finally {
      editor.destroy()
      element.remove()
    }
  })

  it("installs the materialize plugin ahead of columnResizing", () => {
    const { editor, element } = createEditor()
    try {
      const materialize = materializeColumnWidthsPluginKey.get(editor.state)
      const resizing = columnResizingPluginKey.get(editor.state)
      expect(materialize).toBeDefined()
      expect(resizing).toBeDefined()

      // ProseMirror walks `handleDOMEvents` in plugin order and stops at the
      // first handler that returns true, so materialisation only gets to run
      // before the drag starts while it sits ahead of `columnResizing`.
      const plugins = editor.state.plugins
      expect(plugins.indexOf(materialize as never)).toBeLessThan(
        plugins.indexOf(resizing as never)
      )
    } finally {
      editor.destroy()
      element.remove()
    }
  })

  it("keeps the derived-width node view once resizing is enabled", () => {
    const { editor, element } = createEditor()
    try {
      editor.commands.insertTable({ rows: 2, cols: 2, withHeaderRow: true })

      // `columnResizing` registers a plain `TableView` through its own plugin
      // props. Only `TracecatTableView` writes percentage widths, so seeing
      // them proves the extension's `addNodeView` still wins.
      const cols = Array.from(
        element.querySelectorAll<HTMLElement>("table > colgroup > col")
      )
      expect(cols).toHaveLength(2)
      expect(cols.map((col) => col.style.width)).toEqual(["50%", "50%"])
    } finally {
      editor.destroy()
      element.remove()
    }
  })
})
