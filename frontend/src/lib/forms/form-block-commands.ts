import type {
  Completion,
  CompletionContext,
  CompletionResult,
} from "@codemirror/autocomplete"
import { EditorSelection, type EditorState } from "@codemirror/state"
import type { EditorView } from "@codemirror/view"

/** A layout entry the slash menu inserts, with the text selected after it. */
export interface FormBlockTemplate {
  label: string
  detail: string
  /** YAML for the entry without the leading `- `, one line per element. */
  lines: string[]
  /** Text inside `lines` to select after inserting, so typing replaces it. */
  select?: string
}

export const FORM_BLOCK_TEMPLATES: FormBlockTemplate[] = [
  {
    label: "heading",
    detail: "Section title",
    lines: ["heading: Section title"],
    select: "Section title",
  },
  {
    label: "text",
    detail: "Paragraph, Markdown",
    lines: ["text: Write some text"],
    select: "Write some text",
  },
  {
    label: "notice",
    detail: "Callout box",
    lines: ["notice: Something to know", "tone: info"],
    select: "Something to know",
  },
  {
    label: "divider",
    detail: "Horizontal rule",
    lines: ["divider: true"],
  },
]

export function fieldBlockTemplate(name: string): FormBlockTemplate {
  return {
    label: `field: ${name}`,
    detail: "Workflow input",
    lines: [`field: ${name}`],
  }
}

const TOP_LEVEL_KEY = /^[^\s#-][^:]*:/
const LAYOUT_KEY = /^layout:\s*(#.*)?$/

/** The `layout:` line, where its section ends, and its list indent. */
function findLayout(state: EditorState) {
  const { doc } = state
  for (let n = 1; n <= doc.lines; n++) {
    const line = doc.line(n)
    if (!LAYOUT_KEY.test(line.text)) continue
    let end = line.to
    let indent: string | null = null
    for (let m = n + 1; m <= doc.lines; m++) {
      const next = doc.line(m)
      if (TOP_LEVEL_KEY.test(next.text)) break
      if (next.text.trim() !== "") end = next.to
      const item = /^(\s*)- /.exec(next.text)
      if (indent === null && item) indent = item[1]
    }
    return { line, end, indent: indent ?? "  " }
  }
  return null
}

/**
 * Replace the `/query` typed at `from..to` with a layout entry. Inside the
 * `layout:` section the entry goes on the current line, or the next one when
 * the line already has text; elsewhere it goes at the end of the section,
 * which is created when missing.
 */
export function insertFormBlock(
  view: EditorView,
  template: FormBlockTemplate,
  from: number,
  to: number
): void {
  const { state } = view
  const line = state.doc.lineAt(from)
  const before = state.sliceDoc(line.from, from)
  const after = state.sliceDoc(to, line.to)
  const layout = findLayout(state)
  const entry = (indent: string) =>
    template.lines
      .map((text, i) => (i === 0 ? `${indent}- ${text}` : `${indent}  ${text}`))
      .join("\n")

  let changeFrom: number
  let changeTo: number
  let insert: string
  let entryStart: number
  let entryText: string

  const inLayout =
    layout !== null &&
    line.number > layout.line.number &&
    from <= layout.end + 1
  const emptyLine = /^\s*-?\s*$/.test(before) && after.trim() === ""

  if (layout && inLayout && emptyLine) {
    changeFrom = line.from
    changeTo = line.to
    entryText = entry(layout.indent)
    insert = entryText
    entryStart = changeFrom
  } else if (layout && inLayout) {
    // Drop the slash query, then add the entry on its own line below.
    const kept = (state.sliceDoc(line.from, from) + after).trimEnd()
    changeFrom = line.from
    changeTo = line.to
    entryText = entry(layout.indent)
    insert = `${kept}\n${entryText}`
    entryStart = changeFrom + kept.length + 1
  } else {
    // Outside the layout section the query is removed and the entry appended.
    const removal = { from, to, insert: "" }
    const removed = to - from
    const anchor = layout ? layout.end : state.doc.length
    const header = layout ? "" : "layout:\n"
    // The text before the anchor once the query is gone.
    const end = anchor === to ? from : anchor
    const pad = end === 0 || state.sliceDoc(end - 1, end) === "\n" ? "" : "\n"
    const body = entry(layout?.indent ?? "  ")
    const text = `${pad}${header}${body}`
    const shift = anchor >= to ? removed : 0
    const start = anchor - shift + pad.length + header.length
    view.dispatch({
      changes: [removal, { from: anchor, insert: text }],
      selection: selectionFor(template, start, body),
      scrollIntoView: true,
    })
    return
  }

  view.dispatch({
    changes: { from: changeFrom, to: changeTo, insert },
    selection: selectionFor(template, entryStart, entryText),
    scrollIntoView: true,
  })
}

function selectionFor(
  template: FormBlockTemplate,
  entryStart: number,
  entryText: string
): EditorSelection {
  const at = template.select ? entryText.indexOf(template.select) : -1
  if (template.select && at >= 0) {
    const anchor = entryStart + at
    return EditorSelection.single(anchor, anchor + template.select.length)
  }
  return EditorSelection.single(entryStart + entryText.length)
}

/** Completion source for `/` at the start of a word in the form config. */
export function formBlockCompletions(
  getFields: () => string[]
): (context: CompletionContext) => CompletionResult | null {
  return (context) => {
    const word = context.matchBefore(/(^|\s)\/[\w-]*$/)
    if (!word) return null
    const slash = word.from + word.text.indexOf("/")
    const templates = [
      ...getFields().map(fieldBlockTemplate),
      ...FORM_BLOCK_TEMPLATES,
    ]
    const options: Completion[] = templates.map((template, index) => ({
      label: `/${template.label}`,
      displayLabel: template.label,
      detail: template.detail,
      type: template.label.startsWith("field:") ? "variable" : "keyword",
      boost: -index,
      apply: (view, _completion, from, to) =>
        insertFormBlock(view, template, from, to),
    }))
    return { from: slash, options, filter: true }
  }
}
