import { EditorState } from "@codemirror/state"
import { EditorView } from "@codemirror/view"
import { ExternalChange } from "@uiw/react-codemirror"
import { parseDocument } from "yaml"
import { skillToolsExtension } from "@/components/editor/codemirror/skill-tools-extension"

function editor(source: string) {
  return EditorState.create({ doc: source, extensions: skillToolsExtension })
}

function visibleSource(state: EditorState) {
  const ranges: [number, number][] = []
  for (const decorations of state.facet(EditorView.decorations)) {
    if (typeof decorations === "function") continue
    decorations.between(0, state.doc.length, (from, to) => {
      ranges.push([from, to])
    })
  }
  let source = state.doc.toString()
  for (const [from, to] of ranges.reverse()) {
    source = source.slice(0, from) + source.slice(to)
  }
  return source
}

it.each([
  "name: example\ndescription: Example\nmetadata:\n  tools:\n    - core.old",
  "metadata: { tools: [core.old] }\nname: example\ndescription: Example",
])("shows only the descriptive YAML while retaining tools: %s", (source) => {
  const state = editor(source)
  expect(visibleSource(state)).not.toContain("metadata")
  expect(visibleSource(state)).not.toContain("core.old")
  expect(visibleSource(state)).toContain("name: example")
  expect(state.doc.toString()).toBe(source)
})

it.each([
  "name: example\nmetadata:\n  tools: [core.old]\n  revision: 0123\n",
  "name: example\nmetadata: {tools: [core.old], revision: 0123}",
  "name: example\nmetadata: {revision: 0123, tools: [core.old]}",
])("preserves other metadata and scalar source: %s", (source) => {
  const visible = visibleSource(editor(source))
  expect(visible).not.toContain("tools")
  expect(visible).toContain("revision: 0123")
  expect(parseDocument(visible).errors).toEqual([])
})

it("allows descriptive edits and picker updates but protects hidden tools", () => {
  const source = "name: example\nmetadata: {tools: [core.old]}"
  let state = editor(source)
  state = state.update({
    changes: { from: 6, to: 13, insert: "updated" },
  }).state
  expect(visibleSource(state)).toContain("name: updated")
  const toolStart = state.doc.toString().indexOf("core.old")
  state = state.update({
    changes: { from: toolStart, to: toolStart + 8, insert: "core.bad" },
  }).state
  expect(state.doc.toString()).toContain("core.old")
  const updated = state.doc.toString().replace("core.old", "core.new")
  state = state.update({
    changes: { from: 0, to: state.doc.length, insert: updated },
    annotations: ExternalChange.of(true),
  }).state
  expect(state.doc.toString()).toContain("core.new")
  expect(visibleSource(state)).not.toContain("core.new")
})

it("keeps tools hidden while a quoted description is incomplete", () => {
  const state = editor("description: Example\nmetadata: {tools: [core.old]}")
  const updated = state.update({ changes: { from: 13, insert: '"' } }).state
  expect(visibleSource(updated)).toContain('description: "Example')
  expect(visibleSource(updated)).not.toContain("core.old")
  expect(updated.doc.toString()).toContain("core.old")
})
