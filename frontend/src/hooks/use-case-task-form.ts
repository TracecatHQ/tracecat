"use client"

import { zodResolver } from "@hookform/resolvers/zod"
import type { BaseSyntheticEvent } from "react"
import { type UseFormReturn, useForm } from "react-hook-form"
import { z } from "zod"
import type { CasePriority, CaseTaskCreate, CaseTaskRead } from "@/client"
import { CASE_TASK_STATUS_VALUES } from "@/components/cases/case-task-status"
import { useCreateCaseTask, useUpdateCaseTask } from "@/lib/hooks"

/**
 * Hard cap on task description length, matching the database column. The
 * card's textarea enforces it via `maxLength` — a silent stop, no error copy —
 * and the schema backstops programmatic writes.
 */
export const CASE_TASK_DESCRIPTION_MAX_LENGTH = 1000

/**
 * Case task priorities accepted by the form. `satisfies` pins the tuple to the
 * generated `CasePriority` union so a backend contract change becomes a
 * compile error instead of a silently drifting hand copy.
 */
const CASE_TASK_PRIORITY_VALUES = [
  "unknown",
  "low",
  "medium",
  "high",
  "critical",
  "other",
] as const satisfies readonly CasePriority[]

/**
 * Schema for the case task card form. Deliberately excludes
 * `default_trigger_values`: the form never edits it, and sending it — even as
 * `null` — would erase trigger defaults written by `WorkflowTriggerDialog`.
 */
export const caseTaskFormSchema = z.object({
  title: z.string().trim().min(1, "Title is required"),
  description: z.string().max(CASE_TASK_DESCRIPTION_MAX_LENGTH),
  status: z.enum(CASE_TASK_STATUS_VALUES),
  priority: z.enum(CASE_TASK_PRIORITY_VALUES),
  assignee_id: z.string().nullable(),
  workflow_id: z.string().nullable(),
})

/** Values held by the case task form. */
export type CaseTaskFormValues = z.infer<typeof caseTaskFormSchema>

/**
 * Blank composer defaults: an empty title and description, `todo`, `unknown`
 * priority, and no assignee or workflow.
 */
export function caseTaskFormDefaults(): CaseTaskFormValues {
  return {
    title: "",
    description: "",
    status: "todo",
    priority: "unknown",
    assignee_id: null,
    workflow_id: null,
  }
}

/** Edit-mode defaults, lifted from the task being edited. */
export function caseTaskFormValuesFromTask(
  task: CaseTaskRead
): CaseTaskFormValues {
  return {
    title: task.title,
    description: task.description ?? "",
    status: task.status,
    priority: task.priority,
    assignee_id: task.assignee?.id ?? null,
    workflow_id: task.workflow_id ?? null,
  }
}

/**
 * Builds the mutation payload key-by-key from the six form fields. Never
 * spread a wider object here: any extra key — `default_trigger_values` above
 * all — must not ride along on create or update requests.
 */
function toCaseTaskPayload(values: CaseTaskFormValues): CaseTaskCreate {
  return {
    title: values.title,
    description: values.description,
    status: values.status,
    priority: values.priority,
    assignee_id: values.assignee_id,
    workflow_id: values.workflow_id,
  }
}

/** Options for {@link useCaseTaskForm}. */
export interface UseCaseTaskFormOptions {
  caseId: string
  workspaceId: string
  /** Task to edit. Omit (or pass `null`) for create mode. */
  task?: CaseTaskRead | null
  /** Called after a successful create. The form has already been reset. */
  onCreateSuccess?: () => void
  /** Called after a successful edit save. */
  onUpdateSuccess?: () => void
}

/** Return value of {@link useCaseTaskForm}. */
export interface UseCaseTaskFormResult {
  form: UseFormReturn<CaseTaskFormValues>
  /** Validating submit handler, ready for `<form onSubmit={...}>`. */
  submit: (event?: BaseSyntheticEvent) => Promise<void>
  isPending: boolean
}

/**
 * Form state for the case task card — the single source of truth for schema,
 * defaults, and the create/update mutations. Without a `task` it backs the
 * new-task composer; with one it backs that card's edit mode, seeded from the
 * task and saving the whole six-field form in one update. Either way the
 * payload is built key-by-key so `default_trigger_values` never rides along.
 */
export function useCaseTaskForm({
  caseId,
  workspaceId,
  task = null,
  onCreateSuccess,
  onUpdateSuccess,
}: UseCaseTaskFormOptions): UseCaseTaskFormResult {
  const { createTask, createTaskIsPending } = useCreateCaseTask({
    caseId,
    workspaceId,
  })
  // Unconditional for the rules of hooks; the empty id is never used because
  // create mode never calls `updateTask`.
  const { updateTask, updateTaskIsPending } = useUpdateCaseTask({
    caseId,
    workspaceId,
    taskId: task?.id ?? "",
  })

  const form = useForm<CaseTaskFormValues>({
    resolver: zodResolver(caseTaskFormSchema),
    defaultValues: task
      ? caseTaskFormValuesFromTask(task)
      : caseTaskFormDefaults(),
  })

  const submit = form.handleSubmit((values) => {
    const payload = toCaseTaskPayload(values)
    if (task) {
      updateTask(payload, {
        onSuccess: () => onUpdateSuccess?.(),
      })
      return
    }
    createTask(payload, {
      onSuccess: () => {
        // Reset for the next entry; the composer stays open for rapid entry
        // and moves focus back to the title itself.
        form.reset(caseTaskFormDefaults())
        onCreateSuccess?.()
      },
    })
  })

  return {
    form,
    submit,
    isPending: task ? updateTaskIsPending : createTaskIsPending,
  }
}
