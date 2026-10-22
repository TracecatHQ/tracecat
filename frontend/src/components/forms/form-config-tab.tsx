"use client"

import { autocompletion } from "@codemirror/autocomplete"
import {
  lintGutter,
  nextDiagnostic,
  openLintPanel,
  setDiagnostics,
} from "@codemirror/lint"
import { type Extension, Prec, StateField, type Text } from "@codemirror/state"
import {
  Decoration,
  type DecorationSet,
  EditorView,
  keymap,
  WidgetType,
} from "@codemirror/view"
import { ImageIcon, WorkflowIcon } from "lucide-react"
import Link from "next/link"
import { type ChangeEvent, useEffect, useRef, useState } from "react"
import type { FormRead } from "@/client"
import { useScopeCheck } from "@/components/auth/scope-guard"
import { CodeEditor } from "@/components/editor/codemirror/code-editor"
import { tracecatSyntaxHighlighting } from "@/components/editor/codemirror/syntax-highlight"
import { Button } from "@/components/ui/button"
import { toast } from "@/components/ui/use-toast"
import type { FormConfigCheck, FormConfigDraft } from "@/hooks/use-form-config"
import { useUpdateForm } from "@/hooks/use-forms"
import { getFormConfigIssues, getFormErrorDetail } from "@/lib/forms/errors"
import { formBlockCompletions } from "@/lib/forms/form-block-commands"
import {
  FORM_IMAGE_TYPES,
  FormImageError,
  findImageDataUris,
  prepareFormImage,
} from "@/lib/forms/image-data-uri"
import {
  type FormConfigProblem,
  locateFormIssues,
  parseFormConfigYaml,
} from "@/lib/forms/yaml-diagnostics"
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
  return Decoration.set(
    findImageDataUris(doc.toString()).map(({ from, to, bytes }) =>
      Decoration.replace({ widget: new ImageChipWidget(bytes) }).range(from, to)
    )
  )
}

/**
 * Fold every inline image into an atomic chip such as "image 18 KB", so a
 * megabyte of base64 stays out of the DOM and the YAML around it stays
 * readable. A state field, not a view plugin, because the replaced ranges
 * are far longer than the viewport.
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

/**
 * Pick a PNG, JPEG or WebP image, shrink it and insert it at the cursor as a
 * quoted `data:` URI, for the `image` of a card option. A comma ends a plain
 * YAML scalar inside `{...}`, hence the quotes, unless the author typed them.
 */
function InsertImageButton({ view }: { view: EditorView | null }) {
  const inputRef = useRef<HTMLInputElement>(null)
  const [isWorking, setIsWorking] = useState(false)

  async function handleFile(event: ChangeEvent<HTMLInputElement>) {
    const file = event.target.files?.[0]
    // Clear the input so picking the same file again fires a change.
    event.target.value = ""
    if (!file || !view) {
      return
    }
    setIsWorking(true)
    try {
      const dataUri = await prepareFormImage(file)
      const { from } = view.state.selection.main
      const before = view.state.sliceDoc(Math.max(0, from - 1), from)
      const quoted = before === '"' || before === "'"
      view.dispatch(
        view.state.replaceSelection(quoted ? dataUri : `"${dataUri}"`)
      )
      view.focus()
    } catch (error) {
      toast({
        title: "Couldn't insert image",
        description:
          error instanceof FormImageError
            ? error.message
            : "Something went wrong. Try another image.",
        variant: "destructive",
      })
    } finally {
      setIsWorking(false)
    }
  }

  return (
    <>
      <input
        ref={inputRef}
        type="file"
        accept={FORM_IMAGE_TYPES.join(",")}
        className="hidden"
        aria-label="Image file"
        onChange={handleFile}
      />
      <Button
        type="button"
        variant="outline"
        size="sm"
        className="h-7 gap-1.5 px-2 text-xs"
        disabled={isWorking || !view}
        onClick={() => inputRef.current?.click()}
      >
        <ImageIcon className="size-3.5" />
        Insert image
      </Button>
    </>
  )
}

/**
 * Editor chrome on the app's own tokens, so it matches the page in both
 * themes instead of the bundled one-dark and default light themes.
 */
const formEditorTheme = Prec.highest(
  EditorView.theme({
    "&": {
      backgroundColor: "hsl(var(--background))",
      color: "hsl(var(--foreground))",
    },
    ".cm-gutters": {
      backgroundColor: "hsl(var(--background))",
      color: "hsl(var(--muted-foreground))",
      borderRight: "1px solid hsl(var(--border))",
    },
    ".cm-activeLine, .cm-activeLineGutter": {
      backgroundColor: "hsl(var(--muted) / 0.5)",
    },
    ".cm-placeholder": { color: "hsl(var(--muted-foreground))" },
    ".cm-cursor, .cm-dropCursor": {
      borderLeftColor: "hsl(var(--foreground))",
    },
    "&.cm-focused .cm-selectionBackground, .cm-selectionBackground, .cm-content ::selection":
      { backgroundColor: "hsl(var(--muted-foreground) / 0.25)" },
    ".cm-gutter-lint": { width: "1rem" },
    ".cm-gutter-lint .cm-gutterElement": {
      display: "flex",
      alignItems: "center",
      justifyContent: "center",
      padding: "0",
    },
    ".cm-lint-marker": {
      width: "0.4rem",
      height: "0.4rem",
      borderRadius: "9999px",
      content: "none",
    },
    ".cm-lint-marker-error": { backgroundColor: "rgb(239 68 68)" },
    ".cm-lint-marker-warning": { backgroundColor: "rgb(245 158 11)" },
    ".cm-lint-marker-info": { backgroundColor: "rgb(59 130 246)" },
    ".cm-lintRange-error": {
      backgroundImage: "none",
      textDecoration: "underline wavy rgb(239 68 68)",
      textUnderlineOffset: "3px",
    },
    ".cm-lintRange-warning": {
      backgroundImage: "none",
      textDecoration: "underline wavy rgb(245 158 11)",
      textUnderlineOffset: "3px",
    },
    ".cm-tooltip": {
      backgroundColor: "hsl(var(--popover))",
      color: "hsl(var(--popover-foreground))",
      border: "1px solid hsl(var(--border))",
      borderRadius: "0.375rem",
      boxShadow: "0 4px 12px rgb(0 0 0 / 0.12)",
      overflow: "hidden",
    },
    ".cm-tooltip-lint": { padding: "0" },
    ".cm-diagnostic": {
      padding: "0.375rem 0.625rem",
      fontSize: "0.75rem",
      borderLeftWidth: "3px",
    },
    ".cm-diagnostic-error": { borderLeftColor: "rgb(239 68 68)" },
    ".cm-diagnostic-warning": { borderLeftColor: "rgb(245 158 11)" },
    ".cm-diagnostic-info": { borderLeftColor: "rgb(59 130 246)" },
    ".cm-tooltip-autocomplete > ul": {
      fontFamily: "inherit",
      fontSize: "0.75rem",
      maxHeight: "16rem",
    },
    ".cm-tooltip-autocomplete > ul > li": { padding: "0.25rem 0.5rem" },
    ".cm-tooltip-autocomplete > ul > li[aria-selected]": {
      backgroundColor: "hsl(var(--accent))",
      color: "hsl(var(--accent-foreground))",
    },
    ".cm-completionDetail": {
      marginLeft: "0.75rem",
      fontStyle: "normal",
      color: "hsl(var(--muted-foreground))",
    },
    ".cm-completionIcon": { display: "none" },
    ".cm-completionMatchedText": {
      textDecoration: "none",
      fontWeight: "600",
    },
  })
)

/**
 * The Config tab of a form: a YAML editor with Save, the problems the server
 * finds marked on their lines, and the workflow the inputs come from. The
 * YAML is sent as JSON, and the buffer is rewritten from the stored config.
 */
export function FormConfigTab({
  form,
  canEdit,
  draft,
  check,
  fields = [],
}: {
  form: FormRead
  /** Whether the caller holds `form:update`. Without it the editor is read-only. */
  canEdit: boolean
  draft: FormConfigDraft
  check: FormConfigCheck
  /** Workflow inputs the slash menu offers as field blocks. */
  fields?: string[]
}) {
  const { mutateAsync: updateForm } = useUpdateForm({
    workspaceId: form.workspace_id,
    formId: form.id,
  })
  const canReadWorkflows = useScopeCheck("workflow:read") === true
  const workflowName =
    typeof form.workflow_version === "number"
      ? `${form.workflow_title} v${form.workflow_version}`
      : form.workflow_title
  const [view, setView] = useState<EditorView | null>(null)
  const [isSaving, setIsSaving] = useState(false)
  // Problems a save was rejected with, for the text that was sent.
  const [rejected, setRejected] = useState<{
    text: string
    problems: FormConfigProblem[]
  } | null>(null)

  const problems =
    rejected?.text === draft.text ? rejected.problems : check.problems
  const errorCount = problems.filter(
    (problem) => problem.severity === "error"
  ).length
  const canSave = canEdit && draft.isDirty && !isSaving && errorCount === 0

  const saveRef = useRef(save)
  saveRef.current = save
  const fieldsRef = useRef(fields)
  fieldsRef.current = fields
  const [extensions] = useState<Extension[]>(() => [
    imageChips,
    chipTheme,
    tracecatSyntaxHighlighting,
    formEditorTheme,
    lintGutter(),
    autocompletion({
      override: [formBlockCompletions(() => fieldsRef.current)],
      icons: false,
    }),
    Prec.highest(
      keymap.of([
        {
          key: "Mod-s",
          preventDefault: true,
          run: () => {
            void saveRef.current()
            return true
          },
        },
      ])
    ),
  ])

  // Mark the problems in the text: underline, gutter, tooltip and lint panel.
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
          const to = Math.max(from, Math.min(problem.to, length))
          return { ...problem, from, to }
        })
      )
    )
  }, [view, problems])

  // While the saved config has changed elsewhere, only Overwrite saves.
  async function save(overwrite = false) {
    if (!canSave || (draft.hasRemoteChange && !overwrite)) {
      return
    }
    const text = draft.text
    // The check runs on a delay, so parse what is on screen right now.
    const parsed = parseFormConfigYaml(text)
    if (!parsed.config) {
      setRejected({ text, problems: parsed.problems })
      return
    }
    setIsSaving(true)
    try {
      // A save goes over the config the edit started from, and is refused
      // if that changed. Overwrite saves over whatever is there.
      const saved = await updateForm(
        overwrite
          ? { config: parsed.config }
          : { config: parsed.config, base_config_version: draft.baseVersion }
      )
      draft.markSaved(saved, text)
      setRejected(null)
      toast({ title: "Form saved" })
    } catch (error) {
      const issues = getFormConfigIssues(error)
      if (issues) {
        setRejected({ text, problems: locateFormIssues(text, issues) })
      } else {
        toast({
          title: "Couldn't save the form",
          description:
            getFormErrorDetail(error)?.message ?? "Something went wrong.",
          variant: "destructive",
        })
      }
    } finally {
      setIsSaving(false)
    }
  }

  // One banner at a time. A remote change needs a decision, so it wins.
  let banner: [text: string, actions: [string, () => void, boolean?][]] | null =
    null
  if (draft.hasRemoteChange) {
    banner = [
      "This form changed elsewhere. Reload to see it and lose your unsaved edits, or overwrite it with yours.",
      [
        ["Reload", draft.reload],
        ["Overwrite", () => save(true), !canSave],
      ],
    ]
  } else if (draft.isRestored) {
    banner = [
      "Unsaved draft restored. Save to keep it.",
      [["Discard draft", draft.reload]],
    ]
  }

  return (
    <div className="flex h-full min-h-0 flex-col">
      <div className="flex h-9 flex-none items-center gap-2 border-b px-3">
        {canEdit ? (
          <>
            <InsertImageButton view={view} />
            <span className="hidden truncate text-xs text-muted-foreground sm:inline">
              Type{" "}
              <kbd className="rounded border bg-muted px-1 font-mono text-[11px]">
                /
              </kbd>{" "}
              to add a block
            </span>
          </>
        ) : (
          <span className="text-xs text-muted-foreground">Read only</span>
        )}
        <span className="flex-1" />
        {errorCount > 0 && (
          <button
            type="button"
            className="truncate text-xs text-muted-foreground hover:text-foreground hover:underline"
            onClick={() => {
              if (view) {
                nextDiagnostic(view)
                openLintPanel(view)
              }
            }}
          >
            {errorCount === 1
              ? "1 error blocks saving"
              : `${errorCount} errors block saving`}
          </button>
        )}
        {canEdit && (
          <Button
            size="sm"
            className="h-7 px-3 text-xs"
            disabled={!canSave || draft.hasRemoteChange}
            onClick={() => save()}
          >
            {isSaving ? "Saving..." : "Save"}
          </Button>
        )}
      </div>

      {banner && !isSaving && (
        <div
          role="status"
          className="flex flex-none items-center gap-2 border-b bg-muted/50 px-3 py-1.5 text-xs"
        >
          <span className="flex-1">{banner[0]}</span>
          {banner[1].map(([label, onClick, disabled]) => (
            <Button
              key={label}
              variant="outline"
              className="h-6 px-2 text-xs"
              disabled={disabled}
              onClick={onClick}
            >
              {label}
            </Button>
          ))}
        </div>
      )}

      <div className="min-h-0 flex-1">
        <CodeEditor
          value={draft.text}
          onChange={draft.setText}
          language="yaml"
          readOnly={!canEdit}
          wrapLongLines
          placeholder={canEdit ? PLACEHOLDER : undefined}
          extensions={extensions}
          onCreateEditor={setView}
          className="h-full rounded-none [&_.cm-editor]:h-full [&_.cm-editor]:rounded-none [&_.cm-editor]:border-0 [&_.cm-scroller]:rounded-none"
        />
      </div>

      <div className="flex h-8 flex-none items-center gap-1.5 border-t px-3 text-xs text-muted-foreground">
        <WorkflowIcon className="size-3.5 flex-none" />
        <span className="flex-none">Inputs from</span>
        {canReadWorkflows ? (
          <Link
            href={`/workspaces/${form.workspace_id}/workflows/${form.workflow_id}`}
            className="truncate font-medium text-foreground hover:underline"
          >
            {workflowName}
          </Link>
        ) : (
          <span className="truncate font-medium text-foreground">
            {workflowName}
          </span>
        )}
        {typeof form.workflow_version !== "number" && (
          <span className="flex-none">(draft, not published)</span>
        )}
      </div>
    </div>
  )
}
