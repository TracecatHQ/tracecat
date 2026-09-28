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
import { Button } from "@/components/ui/button"
import {
  Dialog,
  DialogContent,
  DialogDescription,
  DialogFooter,
  DialogHeader,
  DialogTitle,
  nonDismissableDialogProps,
} from "@/components/ui/dialog"

/** Shared responsive shell for the expandable field editors (long text, JSON). */
export const FIELD_EDITOR_DIALOG_CLASS =
  "flex h-[70vh] max-h-[min(42rem,calc(100dvh-2rem))] w-[calc(100vw-2rem)] max-w-4xl flex-col gap-0 overflow-hidden p-0"

// -- Long text dialog --

interface LongTextFieldDialogProps {
  open: boolean
  onOpenChange: (open: boolean) => void
  fieldLabel: string
  initialValue: string
  onSave: (value: string) => void
}

/**
 * Dialog for editing a LONG_TEXT case field using the rich-text editor.
 */
export function LongTextFieldDialog({
  open,
  onOpenChange,
  fieldLabel,
  initialValue,
  onSave,
}: LongTextFieldDialogProps) {
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
    <Dialog open={open} onOpenChange={onOpenChange}>
      <DialogContent
        className={FIELD_EDITOR_DIALOG_CLASS}
        {...nonDismissableDialogProps}
      >
        <DialogHeader className="shrink-0 space-y-1 border-b px-6 py-4 pr-14">
          <DialogTitle>{fieldLabel}</DialogTitle>
          <DialogDescription>
            Edit the rich text content for this field.
          </DialogDescription>
        </DialogHeader>
        <div className="min-h-0 flex-1 overflow-hidden">
          <CaseDescriptionEditor
            className="case-description-editor--dialog"
            initialContent={draft}
            onChange={setDraft}
            autoFocus
          />
        </div>
        <DialogFooter className="shrink-0 border-t px-6 py-3">
          <Button variant="outline" onClick={handleSave}>
            Save
          </Button>
        </DialogFooter>
      </DialogContent>
    </Dialog>
  )
}

// -- JSON dialog --

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

interface JsonFieldDialogProps {
  open: boolean
  onOpenChange: (open: boolean) => void
  fieldLabel: string
  initialValue: unknown
  onSave: (value: unknown) => void
}

/**
 * Dialog for editing a JSONB case field using a CodeMirror JSON editor
 * with syntax highlighting, linting, and validation.
 */
export function JsonFieldDialog({
  open,
  onOpenChange,
  fieldLabel,
  initialValue,
  onSave,
}: JsonFieldDialogProps) {
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
    <Dialog open={open} onOpenChange={onOpenChange}>
      <DialogContent
        className={FIELD_EDITOR_DIALOG_CLASS}
        {...nonDismissableDialogProps}
      >
        <DialogHeader className="shrink-0 space-y-1 border-b px-6 py-4 pr-14">
          <DialogTitle>{fieldLabel}</DialogTitle>
          <DialogDescription>
            Edit the JSON value for this field.
          </DialogDescription>
        </DialogHeader>
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
          <p className="shrink-0 px-6 py-2 text-xs text-destructive">{error}</p>
        )}
        <DialogFooter className="shrink-0 border-t px-6 py-3">
          <Button variant="outline" onClick={handleSave} disabled={!isValid}>
            Save
          </Button>
        </DialogFooter>
      </DialogContent>
    </Dialog>
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
