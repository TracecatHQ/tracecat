import { EditorState, StateField } from "@codemirror/state"
import { Decoration, type DecorationSet, EditorView } from "@codemirror/view"
import { ExternalChange } from "@uiw/react-codemirror"
import { isMap, isNode, isScalar, parseDocument } from "yaml"

function hiddenTools(source: string): DecorationSet {
  const document = parseDocument(source)
  const root = document.contents
  if (!isMap(root)) return Decoration.none
  const metadata = document.get("metadata", true)
  if (!isMap(metadata) || !metadata.has("tools")) return Decoration.none

  const mapping = metadata.items.length === 1 ? root : metadata
  const key = metadata.items.length === 1 ? "metadata" : "tools"
  const index = mapping.items.findIndex(
    (pair) => isScalar(pair.key) && pair.key.value === key
  )
  const pair = mapping.items[index]
  if (!pair || !isNode(pair.key) || !pair.key.range) return Decoration.none
  if (!isNode(pair.value) || !pair.value.range) return Decoration.none
  let from = pair.key.range[0]
  let to = pair.value.range[2]
  if (mapping.flow) {
    const next = mapping.items[index + 1]?.key
    const previous = mapping.items[index - 1]?.value
    if (isNode(next) && next.range) {
      to = next.range[0]
    } else if (isNode(previous) && previous.range) {
      from = previous.range[1]
    }
  } else {
    from = source.lastIndexOf("\n", from - 1) + 1
    // A trailing hidden entry owns its separator too, so it leaves no blank
    // editable line after the last visible field.
    if (to === source.length && from > 0) {
      from -= source[from - 2] === "\r" ? 2 : 1
    }
  }
  return Decoration.set([
    Decoration.replace({ inclusive: false }).range(from, to),
  ])
}

const toolsField = StateField.define<DecorationSet>({
  create: (state) => hiddenTools(state.doc.toString()),
  update: (decorations, transaction) => {
    if (!transaction.docChanged) return decorations
    if (transaction.annotation(ExternalChange)) {
      return hiddenTools(transaction.newDoc.toString())
    }
    // Keep the picker-owned region hidden while visible YAML is mid-edit.
    return decorations.map(transaction.changes)
  },
  provide: (field) => [
    EditorView.decorations.from(field),
    EditorView.atomicRanges.of((view) => view.state.field(field)),
  ],
})

/** Hide picker-owned tools without removing them from the saved YAML source. */
export const skillToolsExtension = [
  toolsField,
  EditorState.changeFilter.of((transaction) => {
    // Controlled updates from the tool picker must still replace the source.
    if (transaction.annotation(ExternalChange)) return true
    const ranges: number[] = []
    transaction.startState
      .field(toolsField)
      .between(0, transaction.startState.doc.length, (from, to) => {
        ranges.push(from, to)
      })
    return ranges
  }),
]
