import type { FormFieldBlock, FormResolved, FormUserRead } from "@/client"

/** One block of a resolved form: a field, heading, text, notice or divider. */
export type FormBlock = FormResolved["blocks"][number]

/** Form state keyed by field name. */
export type FormValues = Record<string, unknown>

/** Lifecycle of one file a requester picked in a `file` field. */
export type FormFileStatus = "selected" | "uploading" | "uploaded" | "failed"

/** A file held in form state. Only `fileId` is submitted. */
export interface FormFileItem {
  /** Stable client-side key. */
  key: string
  name: string
  size: number
  status: FormFileStatus
  /** Set once the upload succeeds. */
  fileId?: string
  /** Why the upload failed. */
  error?: string
}

/** Whether a block is an input the requester fills in. */
export function isFieldBlock(block: FormBlock): block is FormFieldBlock {
  return block.type === "field"
}

/**
 * Return the fields a requester with these values sees, in block order.
 *
 * Mirrors `visible_fields` in `tracecat/forms/values.py`: a conditional field
 * shows while its controller strictly equals the rule's value, and a
 * controller the submission leaves out counts as its default. Empty answers
 * are left out (see `toSubmissionValues`), so a cleared controller falls back
 * to its default here exactly as it does on the server.
 */
export function getVisibleFields(
  blocks: FormBlock[],
  values: FormValues
): FormFieldBlock[] {
  const fields = blocks.filter(isFieldBlock)
  const byName = new Map(fields.map((field) => [field.name, field]))
  return fields.filter((field) => {
    const rule = field.visible_if
    if (!rule) {
      return true
    }
    const controller = byName.get(rule.field)
    if (!controller) {
      return false
    }
    const answer = toSubmittedValue(controller, values[rule.field])
    const value = isEmptyFormValue(answer) ? controller.default : answer
    return value === rule.equals
  })
}

/** Whether a value counts as not filled in. `false` is a valid toggle. */
export function isEmptyFormValue(value: unknown): boolean {
  return (
    value === undefined ||
    value === null ||
    value === "" ||
    (Array.isArray(value) && value.length === 0)
  )
}

function toSubmittedValue(field: FormFieldBlock, value: unknown): unknown {
  if (field.widget === "user") {
    const users = (Array.isArray(value) ? value : [value]) as FormUserRead[]
    const ids = users.filter(Boolean).map((user) => user.user_id)
    return field.multiple ? ids : ids[0]
  }
  if (field.widget === "file") {
    const ids = ((value ?? []) as FormFileItem[]).flatMap((item) =>
      item.fileId ? [item.fileId] : []
    )
    return field.multiple ? ids : ids[0]
  }
  return value
}

/**
 * Build the `values` of a submission from form state.
 *
 * Hidden fields and empty answers are left out. `user` fields send user IDs
 * and `file` fields send uploaded file IDs; the server expands both.
 */
export function toSubmissionValues(
  blocks: FormBlock[],
  values: FormValues
): FormValues {
  const submitted: FormValues = {}
  for (const field of getVisibleFields(blocks, values)) {
    const value = toSubmittedValue(field, values[field.name])
    if (!isEmptyFormValue(value)) {
      submitted[field.name] = value
    }
  }
  return submitted
}
