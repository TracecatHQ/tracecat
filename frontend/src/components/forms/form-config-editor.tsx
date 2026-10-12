"use client"

import { lintGutter, setDiagnostics } from "@codemirror/lint"
import {
  type Extension,
  Prec,
  RangeSetBuilder,
  StateField,
  type Text,
} from "@codemirror/state"
import {
  Decoration,
  type DecorationSet,
  EditorView,
  keymap,
  WidgetType,
} from "@codemirror/view"
import { useEffect, useMemo, useRef, useState } from "react"
import { CodeEditor } from "@/components/editor/codemirror/code-editor"
import { findImageDataUris } from "@/lib/forms/image-data-uri"
import type { FormConfigProblem } from "@/lib/forms/yaml-diagnostics"
import { formatFileSize } from "@/lib/utils"

const PLACEHOLDER = `title: Laptop request
description: Request a new laptop from IT.
layout:
  - field: model
    widget: cards`

class ImageChipWidget extends WidgetType {
  constructor(private readonly bytes: number) {
    super()
  }

  eq(other: ImageChipWidget): boolean {
    return other.bytes === this.bytes
  }

  toDOM(): HTMLElement {
    const chip = document.createElement("span")
    chip.className = "cm-form-image-chip"
    chip.textContent = `image ${formatFileSize(this.bytes)}`
    chip.title = "Inline image. Delete it like a single character."
    return chip
  }
}

function buildImageChips(doc: Text): DecorationSet {
  const builder = new RangeSetBuilder<Decoration>()
  for (const { from, to, bytes } of findImageDataUris(doc.toString())) {
    builder.add(
      from,
      to,
      Decoration.replace({ widget: new ImageChipWidget(bytes) })
    )
  }
  return builder.finish()
}

/**
 * Fold every inline image into a chip such as "image 18 KB".
 *
 * A config can hold a megabyte of base64. Replacing each data URI keeps
 * those characters out of the DOM, so the editor stays fast and the YAML
 * around them stays readable. The chips are atomic: the cursor steps over
 * one and backspace removes it whole. A state field, not a view plugin,
 * because the replaced ranges are far longer than the viewport.
 */
const imageChips = StateField.define<DecorationSet>({
  create: (state) => buildImageChips(state.doc),
  update: (chips, transaction) =>
    transaction.docChanged ? buildImageChips(transaction.newDoc) : chips,
  provide: (field) => [
    EditorView.decorations.from(field),
    EditorView.atomicRanges.of((view) => view.state.field(field)),
  ],
})

const chipTheme = EditorView.baseTheme({
  ".cm-form-image-chip": {
    display: "inline-block",
    padding: "0 6px",
    border: "1px solid hsl(var(--border))",
    borderRadius: "4px",
    backgroundColor: "hsl(var(--muted))",
    color: "hsl(var(--muted-foreground))",
    fontSize: "11px",
    lineHeight: "16px",
  },
})

/** Props of {@link FormConfigEditor}. */
export interface FormConfigEditorProps {
  value: string
  onChange: (value: string) => void
  readOnly: boolean
  /** Marked in the text and the gutter. Offsets outside the text are clamped. */
  problems: readonly FormConfigProblem[]
  /** Called on `Mod-s`. */
  onSave: () => void
  /** Hands out the editor view, for inserting at the cursor. */
  onCreateEditor?: (view: EditorView) => void
}

/**
 * The YAML editor of a form config.
 *
 * Plain text in, plain text out: parsing, checking and saving belong to the
 * caller. This adds the pieces that need the editor itself, which are the
 * problem markers, the image chips and the save shortcut.
 */
export function FormConfigEditor({
  value,
  onChange,
  readOnly,
  problems,
  onSave,
  onCreateEditor,
}: FormConfigEditorProps) {
  const [view, setView] = useState<EditorView | null>(null)
  const onSaveRef = useRef(onSave)
  onSaveRef.current = onSave

  const extensions = useMemo<Extension[]>(
    () => [
      imageChips,
      chipTheme,
      lintGutter(),
      Prec.highest(
        keymap.of([
          {
            key: "Mod-s",
            preventDefault: true,
            run: () => {
              onSaveRef.current()
              return true
            },
          },
        ])
      ),
    ],
    []
  )

  useEffect(() => {
    if (!view) {
      return
    }
    const length = view.state.doc.length
    view.dispatch(
      setDiagnostics(
        view.state,
        problems.map((problem) => {
          const from = Math.min(problem.from, length)
          return {
            from,
            to: Math.max(from, Math.min(problem.to, length)),
            severity: problem.severity,
            message: problem.message,
          }
        })
      )
    )
  }, [view, problems])

  return (
    <CodeEditor
      value={value}
      onChange={onChange}
      language="yaml"
      readOnly={readOnly}
      wrapLongLines
      placeholder={readOnly ? undefined : PLACEHOLDER}
      extensions={extensions}
      onCreateEditor={(created) => {
        setView(created)
        onCreateEditor?.(created)
      }}
      className="h-full rounded-none [&_.cm-editor]:h-full [&_.cm-editor]:rounded-none [&_.cm-editor]:border-0 [&_.cm-scroller]:rounded-none"
    />
  )
}
