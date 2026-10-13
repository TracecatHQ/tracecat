"use client"

import type { EditorView } from "@codemirror/view"
import { CircleXIcon, TriangleAlertIcon, WorkflowIcon } from "lucide-react"
import Link from "next/link"
import { useMemo, useState } from "react"
import type { FormRead } from "@/client"
import { FormConfigEditor } from "@/components/forms/form-config-editor"
import { InsertImageButton } from "@/components/forms/insert-image-button"
import { Button } from "@/components/ui/button"
import { toast } from "@/components/ui/use-toast"
import type { FormConfigCheck, FormConfigDraft } from "@/hooks/use-form-config"
import { useUpdateForm } from "@/hooks/use-forms"
import { getFormConfigIssues, getFormErrorDetail } from "@/lib/forms/errors"
import { formWorkflowHref } from "@/lib/forms/workflow-id"
import {
  type FormConfigProblem,
  locateFormIssues,
  parseFormConfigYaml,
} from "@/lib/forms/yaml-diagnostics"
import { cn } from "@/lib/utils"

/** Props of {@link FormConfigTab}. */
export interface FormConfigTabProps {
  form: FormRead
  /** Whether the caller holds `form:update`. Without it the editor is read-only. */
  canEdit: boolean
  draft: FormConfigDraft
  check: FormConfigCheck
}

function ProblemsList({
  problems,
  onSelect,
}: {
  problems: readonly FormConfigProblem[]
  onSelect: (problem: FormConfigProblem) => void
}) {
  if (problems.length === 0) {
    return null
  }
  return (
    <section
      aria-label="Problems"
      className="max-h-40 flex-none overflow-y-auto border-t"
    >
      <h3 className="flex items-center gap-2 px-3 pt-2 text-xs font-medium">
        Problems
        <span className="rounded-full bg-muted px-1.5 text-[10px] text-muted-foreground">
          {problems.length}
        </span>
      </h3>
      <ul className="py-1">
        {problems.map((problem, index) => {
          const isError = problem.severity === "error"
          const Icon = isError ? CircleXIcon : TriangleAlertIcon
          return (
            <li key={`${problem.from}-${index}`}>
              <button
                type="button"
                className="flex w-full items-start gap-2 px-3 py-1 text-left text-xs hover:bg-muted/50 focus-visible:bg-muted/50 focus-visible:outline-none"
                onClick={() => onSelect(problem)}
              >
                <Icon
                  role="img"
                  aria-label={isError ? "Error" : "Warning"}
                  className={cn(
                    "mt-0.5 size-3.5 flex-none",
                    isError ? "text-destructive" : "text-amber-500"
                  )}
                />
                <span className="min-w-0 flex-1 break-words">
                  {problem.message}
                </span>
                <span className="flex-none text-muted-foreground">
                  Ln {problem.line}
                </span>
              </button>
            </li>
          )
        })}
      </ul>
    </section>
  )
}

/**
 * The Config tab of a form: a YAML editor with Save, the problems the
 * server finds, and which workflow version the inputs come from.
 *
 * There is no visual editor. The YAML is parsed to JSON here and sent as
 * JSON; the server answers with the config it stored, and the buffer is
 * rewritten from that.
 */
export function FormConfigTab({
  form,
  canEdit,
  draft,
  check,
}: FormConfigTabProps) {
  const { updateForm } = useUpdateForm({
    workspaceId: form.workspace_id,
    formId: form.id,
  })
  const [view, setView] = useState<EditorView | null>(null)
  const [isSaving, setIsSaving] = useState(false)
  // Problems a save was rejected with, for the text that was sent.
  const [rejected, setRejected] = useState<{
    text: string
    problems: FormConfigProblem[]
  } | null>(null)

  // A viewer cannot run the check, so they see the saved config's issues.
  const savedProblems = useMemo(
    () => (canEdit ? [] : locateFormIssues(draft.text, form.issues)),
    [canEdit, draft.text, form.issues]
  )
  let problems: readonly FormConfigProblem[] = savedProblems
  if (rejected?.text === draft.text) {
    problems = rejected.problems
  } else if (canEdit) {
    problems = check.problems
  }
  const errorCount = problems.filter(
    (problem) => problem.severity === "error"
  ).length
  const canSave = canEdit && draft.isDirty && !isSaving && errorCount === 0

  /**
   * Save the buffer. While the saved config has changed elsewhere, only an
   * explicit overwrite goes through: Save and the editor shortcut do nothing.
   */
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
      const saved = await updateForm({ config: parsed.config })
      draft.markSaved(saved.config)
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

  function goToProblem(problem: FormConfigProblem) {
    if (!view) {
      return
    }
    const anchor = Math.min(problem.from, view.state.doc.length)
    view.dispatch({ selection: { anchor }, scrollIntoView: true })
    view.focus()
  }

  return (
    <div className="flex h-full min-h-0 flex-col">
      <div className="flex h-9 flex-none items-center gap-2 border-b px-3">
        {canEdit ? (
          <InsertImageButton view={view} />
        ) : (
          <span className="text-xs text-muted-foreground">Read only</span>
        )}
        <span className="flex-1" />
        {errorCount > 0 && (
          <span className="truncate text-xs text-muted-foreground">
            {errorCount === 1
              ? "1 error blocks saving"
              : `${errorCount} errors block saving`}
          </span>
        )}
        {canEdit && (
          <Button
            type="button"
            size="sm"
            className="h-7 px-3 text-xs"
            disabled={!canSave || draft.hasRemoteChange}
            onClick={() => save()}
          >
            {isSaving ? "Saving..." : "Save"}
          </Button>
        )}
      </div>

      {draft.isRestored && !isSaving && (
        <div
          role="status"
          className="flex flex-none items-center gap-2 border-b bg-muted/50 px-3 py-1.5 text-xs"
        >
          <span className="flex-1">
            Unsaved draft restored. Save to keep it.
          </span>
          <Button
            type="button"
            variant="outline"
            size="sm"
            className="h-6 px-2 text-xs"
            onClick={draft.reload}
          >
            Discard draft
          </Button>
        </div>
      )}

      {draft.hasRemoteChange && !isSaving && (
        <div
          role="status"
          className="flex flex-none items-center gap-2 border-b bg-muted/50 px-3 py-1.5 text-xs"
        >
          <span className="flex-1">
            This form changed elsewhere. Reload to see it and lose your unsaved
            edits, or overwrite it with yours.
          </span>
          <Button
            type="button"
            variant="outline"
            size="sm"
            className="h-6 px-2 text-xs"
            onClick={draft.reload}
          >
            Reload
          </Button>
          {canEdit && (
            <Button
              type="button"
              variant="outline"
              size="sm"
              className="h-6 px-2 text-xs"
              disabled={!canSave}
              onClick={() => save(true)}
            >
              Overwrite
            </Button>
          )}
        </div>
      )}

      <div className="min-h-0 flex-1">
        <FormConfigEditor
          value={draft.text}
          onChange={draft.setText}
          readOnly={!canEdit}
          problems={problems}
          onSave={() => save()}
          onCreateEditor={setView}
        />
      </div>

      <ProblemsList problems={problems} onSelect={goToProblem} />

      <div className="flex h-8 flex-none items-center gap-1.5 border-t px-3 text-xs text-muted-foreground">
        <WorkflowIcon className="size-3.5 flex-none" />
        <span className="flex-none">Inputs from</span>
        <Link
          href={formWorkflowHref(form.workspace_id, form.workflow_id)}
          className="truncate font-medium text-foreground hover:underline"
        >
          {form.workflow_title}
          {typeof form.workflow_version === "number" &&
            ` v${form.workflow_version}`}
        </Link>
        {typeof form.workflow_version !== "number" && (
          <span className="flex-none">(draft, not published)</span>
        )}
      </div>
    </div>
  )
}
