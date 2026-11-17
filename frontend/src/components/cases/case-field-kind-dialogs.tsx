"use client"

import { closeBrackets } from "@codemirror/autocomplete"
import { history } from "@codemirror/commands"
import { json } from "@codemirror/lang-json"
import { bracketMatching } from "@codemirror/language"
import { type Diagnostic, linter, lintGutter } from "@codemirror/lint"
import { EditorView } from "@codemirror/view"
import CodeMirror from "@uiw/react-codemirror"
import { useTheme } from "next-themes"
import { useCallback, useEffect, useMemo, useState } from "react"
import { CaseDescriptionEditor } from "@/components/cases/case-description-editor"
import { CaseValueDrawer } from "@/components/cases/case-value-drawer"
import { tracecatSyntaxHighlighting } from "@/components/editor/codemirror/syntax-highlight"
import { Button } from "@/components/ui/button"

// The expandable field editors (long text, JSON) open in the case page's
// value drawer rather than a dialog, so the chat beside the case stays usable
// while a value is open.

// -- Long text drawer --

interface LongTextFieldDrawerProps {
  open: boolean
  onOpenChange: (open: boolean) => void
  fieldLabel: string
  initialValue: string
  onSave: (value: string) => void
}

/**
 * Drawer for editing a LONG_TEXT case field using the rich-text editor.
 */
export function LongTextFieldDrawer({
  open,
  onOpenChange,
  fieldLabel,
  initialValue,
  onSave,
}: LongTextFieldDrawerProps) {
  const [draft, setDraft] = useState(initialValue)

  useEffect(() => {
    if (open) {
      setDraft(initialValue)
    }
  }, [open, initialValue])

  const handleSave = useCallback(() => {
    onSave(draft)
    onOpenChange(false)
  }, [draft, onSave, onOpenChange])

  return (
    <CaseValueDrawer
      open={open}
      onOpenChange={onOpenChange}
      title={fieldLabel}
      description="Edit the rich text content for this field."
    >
      <div className="flex h-full flex-col">
        <div className="min-h-0 flex-1 overflow-hidden">
          <CaseDescriptionEditor
            className="case-description-editor--dialog"
            initialContent={draft}
            onChange={setDraft}
            autoFocus
          />
        </div>
        <div className="flex shrink-0 justify-end border-t px-4 py-3">
          <Button variant="outline" onClick={handleSave}>
            Save
          </Button>
        </div>
      </div>
    </CaseValueDrawer>
  )
}

// -- JSON drawer --

function jsonLinter(view: EditorView): Diagnostic[] {
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

interface JsonFieldDrawerProps {
  open: boolean
  onOpenChange: (open: boolean) => void
  fieldLabel: string
  initialValue: unknown
  onSave: (value: unknown) => void
}

/**
 * Drawer for editing a JSONB case field using a CodeMirror JSON editor
 * with syntax highlighting, linting, and validation.
 */
export function JsonFieldDrawer({
  open,
  onOpenChange,
  fieldLabel,
  initialValue,
  onSave,
}: JsonFieldDrawerProps) {
  const { resolvedTheme } = useTheme()
  const codeMirrorTheme = resolvedTheme === "dark" ? "dark" : "light"
  const serialized =
    initialValue === null || initialValue === undefined
      ? ""
      : JSON.stringify(initialValue, null, 2)

  const [draft, setDraft] = useState(serialized)
  const [error, setError] = useState<string | null>(null)

  useEffect(() => {
    if (open) {
      setDraft(serialized)
      setError(null)
    }
  }, [open, serialized])

  const validate = useCallback((val: string): boolean => {
    if (val.trim() === "") return true
    try {
      JSON.parse(val)
      return true
    } catch {
      return false
    }
  }, [])

  const handleSave = useCallback(() => {
    if (!validate(draft)) {
      setError("Invalid JSON")
      return
    }
    const trimmed = draft.trim()
    onSave(trimmed === "" ? null : JSON.parse(trimmed))
    onOpenChange(false)
  }, [draft, validate, onSave, onOpenChange])

  const extensions = useMemo(
    () => [
      json(),
      tracecatSyntaxHighlighting,
      lintGutter(),
      linter(jsonLinter),
      history(),
      bracketMatching(),
      closeBrackets(),
      EditorView.theme({
        ".cm-content": { fontFamily: "monospace", fontSize: "13px" },
        ".cm-scroller": { overflow: "auto" },
      }),
    ],
    []
  )

  const isValid = validate(draft)

  return (
    <CaseValueDrawer
      open={open}
      onOpenChange={onOpenChange}
      title={fieldLabel}
      description="Edit the JSON value for this field."
    >
      <div className="flex h-full flex-col">
        <div className="min-h-0 flex-1 overflow-hidden">
          <CodeMirror
            value={draft}
            onChange={(val) => {
              setDraft(val)
              if (error) setError(validate(val) ? null : "Invalid JSON")
            }}
            height="100%"
            theme={codeMirrorTheme}
            extensions={extensions}
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
            className="h-full overflow-auto font-mono text-sm [&_.cm-editor]:h-full"
          />
        </div>
        {error && (
          <p className="shrink-0 px-4 py-2 text-xs text-destructive">{error}</p>
        )}
        <div className="flex shrink-0 justify-end border-t px-4 py-3">
          <Button variant="outline" onClick={handleSave} disabled={!isValid}>
            Save
          </Button>
        </div>
      </div>
    </CaseValueDrawer>
  )
}

// -- Inline renderers for the case panel --

interface ExpandFieldCellProps {
  onClick: () => void
  hasValue: boolean
}

/**
 * Inline cell for expandable fields (LONG_TEXT, JSONB): shows "Expand" or "Add..." button.
 */
export function ExpandFieldCell({ onClick, hasValue }: ExpandFieldCellProps) {
  return (
    <Button
      variant="ghost"
      size="sm"
      className="h-7 w-full justify-end px-2 text-sm font-normal text-muted-foreground"
      onClick={onClick}
    >
      {hasValue ? "Expand" : "Add..."}
    </Button>
  )
}
