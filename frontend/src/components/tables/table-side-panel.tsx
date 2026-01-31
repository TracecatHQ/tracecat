"use client"

import { closeBrackets } from "@codemirror/autocomplete"
import { history } from "@codemirror/commands"
import { json } from "@codemirror/lang-json"
import {
  bracketMatching,
  defaultHighlightStyle,
  syntaxHighlighting,
} from "@codemirror/language"
import { type Diagnostic, linter, lintGutter } from "@codemirror/lint"
import { EditorView } from "@codemirror/view"
import CodeMirror from "@uiw/react-codemirror"
import { useCallback, useEffect, useMemo, useState } from "react"
import { useTablePanel } from "@/components/tables/table-panel-context"
import { SimpleEditor } from "@/components/tiptap-templates/simple/simple-editor"
import { Button } from "@/components/ui/button"

function jsonSheetLinter(view: EditorView): Diagnostic[] {
  const content = view.state.doc.toString()
  if (!content.trim()) return []
  try {
    JSON.parse(content)
    return []
  } catch (err) {
    const msg = err instanceof Error ? err.message : "Invalid JSON"
    const posMatch = msg.match(/position (\d+)/)
    const pos = posMatch ? Number.parseInt(posMatch[1], 10) : 0
    const from = Math.min(pos, content.length)
    const to = Math.min(from + 1, content.length)
    return [{ from, to, severity: "error", message: msg, source: "json" }]
  }
}

export function TableSidePanelContent() {
  const { panelContent } = useTablePanel()

  if (!panelContent) return null

  switch (panelContent.mode) {
    case "view-json":
      return <ViewJsonPanel value={panelContent.value} />
    case "edit-text":
      return (
        <EditTextPanel
          value={String(panelContent.value ?? "")}
          onSave={panelContent.onSave}
        />
      )
    case "edit-json":
      return (
        <EditJsonPanel
          value={panelContent.value}
          onSave={panelContent.onSave}
        />
      )
  }
}

const viewJsonExtensions = [
  json(),
  lintGutter(),
  syntaxHighlighting(defaultHighlightStyle),
  bracketMatching(),
  EditorView.theme({
    ".cm-content": { fontFamily: "monospace", fontSize: "12px" },
    ".cm-scroller": { overflow: "auto" },
  }),
]

function ViewJsonPanel({ value }: { value: unknown }) {
  const serialized =
    typeof value === "string"
      ? value
      : value === null || value === undefined
        ? ""
        : JSON.stringify(value, null, 2)

  return (
    <div className="flex h-full flex-col">
      <div className="flex-1 overflow-hidden p-4">
        <CodeMirror
          value={serialized}
          readOnly={true}
          editable={false}
          height="100%"
          extensions={viewJsonExtensions}
          theme="light"
          basicSetup={{
            lineNumbers: true,
            foldGutter: true,
            highlightActiveLine: false,
            bracketMatching: false,
            closeBrackets: false,
            history: false,
            defaultKeymap: false,
            syntaxHighlighting: false,
            autocompletion: false,
          }}
          className="h-full overflow-auto rounded-md border font-mono text-xs"
        />
      </div>
    </div>
  )
}

function EditTextPanel({
  value,
  onSave,
}: {
  value: string
  onSave?: (value: unknown) => void
}) {
  const { closePanel } = useTablePanel()
  const [localValue, setLocalValue] = useState(value)

  // Reset when value changes (new cell opened)
  useEffect(() => {
    setLocalValue(value)
  }, [value])

  return (
    <div className="flex h-full flex-col">
      <div className="flex-1 overflow-y-auto p-4">
        <SimpleEditor
          value={localValue}
          onChange={setLocalValue}
          editable={true}
          showToolbar={true}
          autoFocus
        />
      </div>
      <div className="flex shrink-0 items-center justify-end gap-2 border-t px-4 py-3">
        <Button variant="outline" size="sm" onClick={closePanel}>
          Cancel
        </Button>
        <Button
          size="sm"
          onClick={() => {
            onSave?.(localValue)
            closePanel()
          }}
        >
          Save
        </Button>
      </div>
    </div>
  )
}

function EditJsonPanel({
  value,
  onSave,
}: {
  value: unknown
  onSave?: (parsed: unknown) => void
}) {
  const { closePanel } = useTablePanel()

  const serialized =
    typeof value === "string"
      ? value
      : value === null || value === undefined
        ? ""
        : JSON.stringify(value, null, 2)

  const [localValue, setLocalValue] = useState(serialized)
  const [error, setError] = useState<string | null>(null)

  // Reset when value changes (new cell opened)
  useEffect(() => {
    setLocalValue(serialized)
    setError(null)
  }, [serialized])

  const handleSave = useCallback(() => {
    const trimmed = localValue.trim()
    if (trimmed === "") {
      onSave?.(null)
      closePanel()
      return
    }
    try {
      const parsed = JSON.parse(trimmed)
      setError(null)
      onSave?.(parsed)
      closePanel()
    } catch {
      setError("Invalid JSON")
    }
  }, [localValue, onSave, closePanel])

  const extensions = useMemo(
    () => [
      json(),
      lintGutter(),
      linter(jsonSheetLinter),
      history(),
      bracketMatching(),
      closeBrackets(),
      EditorView.theme({
        ".cm-content": { fontFamily: "monospace", fontSize: "12px" },
        ".cm-scroller": { overflow: "auto" },
      }),
    ],
    []
  )

  return (
    <div className="flex h-full flex-col">
      <div className="flex-1 overflow-hidden p-4">
        <CodeMirror
          value={localValue}
          onChange={(val) => {
            setLocalValue(val)
            if (error) {
              try {
                JSON.parse(val)
                setError(null)
              } catch {
                // keep error
              }
            }
          }}
          height="100%"
          extensions={extensions}
          theme="light"
          autoFocus
          basicSetup={{
            lineNumbers: true,
            foldGutter: true,
            highlightActiveLine: true,
            bracketMatching: false,
            closeBrackets: false,
            history: false,
            defaultKeymap: true,
            syntaxHighlighting: true,
            autocompletion: false,
          }}
          className="h-full overflow-auto rounded-md border font-mono text-xs"
        />
      </div>
      {error && (
        <div className="shrink-0 px-4 pb-2">
          <p className="text-xs text-destructive">{error}</p>
        </div>
      )}
      <div className="flex shrink-0 items-center justify-end gap-2 border-t px-4 py-3">
        <Button variant="outline" size="sm" onClick={closePanel}>
          Cancel
        </Button>
        <Button size="sm" onClick={handleSave}>
          Save
        </Button>
      </div>
    </div>
  )
}
