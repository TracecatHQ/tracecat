import { EditorState } from "@codemirror/state"
import { EditorView } from "@codemirror/view"
import {
  FORM_BLOCK_TEMPLATES,
  fieldBlockTemplate,
  insertFormBlock,
} from "@/lib/forms/form-block-commands"

const template = (label: string) => {
  const found = FORM_BLOCK_TEMPLATES.find((t) => t.label === label)
  if (!found) throw new Error(label)
  return found
}

/** Run an insert on `doc`, where `|` marks the cursor after a `/query`. */
function run(doc: string, query: string, block = template("heading")) {
  const cursor = doc.indexOf("|")
  const text = doc.slice(0, cursor) + doc.slice(cursor + 1)
  const view = new EditorView({
    state: EditorState.create({ doc: text, selection: { anchor: cursor } }),
  })
  insertFormBlock(view, block, cursor - query.length, cursor)
  const { from, to } = view.state.selection.main
  const result = view.state.doc.toString()
  view.destroy()
  return { doc: result, selected: result.slice(from, to) }
}

describe("insertFormBlock", () => {
  it("replaces a slash on an empty layout line with the entry", () => {
    const { doc, selected } = run(
      "layout:\n  - field: model\n  /hea|\n",
      "/hea"
    )
    expect(doc).toBe("layout:\n  - field: model\n  - heading: Section title\n")
    expect(selected).toBe("Section title")
  })

  it("follows the list indent already used in the layout", () => {
    const { doc } = run("layout:\n- field: model\n/|", "/", template("divider"))
    expect(doc).toBe("layout:\n- field: model\n- divider: true")
  })

  it("indents the remaining lines of a multi-line entry", () => {
    const { doc, selected } = run("layout:\n  /|", "/", template("notice"))
    expect(doc).toBe("layout:\n  - notice: Something to know\n    tone: info")
    expect(selected).toBe("Something to know")
  })

  it("adds the entry below a line that already has text", () => {
    const { doc } = run(
      "layout:\n  - field: model /|\n",
      "/",
      fieldBlockTemplate("reason")
    )
    expect(doc).toBe("layout:\n  - field: model\n  - field: reason\n")
  })

  it("appends to the layout section when the slash is above it", () => {
    const { doc } = run(
      "title: Request /|\nlayout:\n  - field: model\nsuccess_message: Done\n",
      "/",
      template("divider")
    )
    expect(doc).toBe(
      "title: Request \nlayout:\n  - field: model\n  - divider: true\nsuccess_message: Done\n"
    )
  })

  it("creates the layout section when there is none", () => {
    const { doc, selected } = run("title: Request\n/|", "/")
    expect(doc).toBe("title: Request\nlayout:\n  - heading: Section title")
    expect(selected).toBe("Section title")
  })
})
