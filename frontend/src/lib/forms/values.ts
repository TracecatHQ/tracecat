import type { FormFieldBlock, FormPreview, FormUserRead } from "@/client"

/** One block of a form: a field, heading, text, notice or divider. */
export type FormBlock = FormPreview["blocks"][number]

/** Form state keyed by input name. */
export type FormValues = Record<string, unknown>

/** A file held in form state. Only `fileId` is submitted. */
export interface FormFileItem {
  /** Stable client-side key. */
  key: string
  name: string
  size: number
  status: "selected" | "uploading" | "uploaded" | "failed"
  /** Set once the upload succeeds. */
  fileId?: string
  /** Why the file was rejected. */
  error?: string
}

/** Whether a block is an input the requester fills in. */
export function isFieldBlock(block: FormBlock): block is FormFieldBlock {
  return block.type === "field"
}

/** Whether a value counts as not filled in. `false` is a valid toggle. */
function isEmptyFormValue(value: unknown): boolean {
  return (
    value === undefined ||
    value === null ||
    value === "" ||
    (Array.isArray(value) && value.length === 0)
  )
}

function getInitialValue(field: FormFieldBlock): unknown {
  switch (field.widget) {
    // A user or file default is an expanded object the client cannot send
    // back as an ID. Leaving the field empty lets the server apply it.
    case "user":
      return field.multiple ? [] : null
    case "file":
      return []
    // Off is an answer. An optional toggle whose default is null starts as
    // null, shown off: untouched, it is not sent and the run gets null.
    case "toggle":
      return field.default ?? (field.required ? false : null)
    default:
      return field.default ?? (field.multiple ? [] : null)
  }
}

/** Starting form state: each field's default, or its widget's empty value. */
export function getInitialFormValues(blocks: FormBlock[]): FormValues {
  return Object.fromEntries(
    blocks
      .filter(isFieldBlock)
      .map((field) => [field.name, getInitialValue(field)])
  )
}

/**
 * Return the fields a requester with these values sees, in block order.
 *
 * Mirrors `visible_fields` in `tracecat/forms/values.py`: a conditional field
 * shows while its controller strictly equals the rule's value, and an empty
 * controller counts as its default, because an empty answer is not sent and
 * the server then applies the default. Only scalar fields can be controllers,
 * so the value in state is the value sent.
 */
export function getVisibleFields(
  blocks: FormBlock[],
  values: FormValues
): FormFieldBlock[] {
  const fields = blocks.filter(isFieldBlock)
  const byName = new Map(fields.map((field) => [field.name, field]))
  return fields.filter((field) => {
    const rule = field.visible_if
    if (!rule) return true
    const controller = byName.get(rule.field)
    if (!controller) return false
    const answer = values[rule.field]
    const value = isEmptyFormValue(answer) ? controller.default : answer
    return value === rule.equals
  })
}

const MAX_SAFE = Number.MAX_SAFE_INTEGER.toLocaleString("en-US")

/**
 * Forms take whole numbers a browser holds exactly, as the server does. A
 * larger one has already lost digits by the time it is in form state.
 */
function isUnsafeInteger(value: unknown): boolean {
  return (
    typeof value === "number" &&
    Number.isInteger(value) &&
    !Number.isSafeInteger(value)
  )
}

/**
 * Check form state before it is submitted: required answers, whole numbers
 * a browser holds exactly, and files that are still uploading or failed.
 * Only visible fields are checked.
 *
 * The server checks everything again, including options and formats, and its
 * errors are shown on the same fields.
 *
 * @returns One message per invalid field, keyed by input name.
 */
export function validateFormValues(
  blocks: FormBlock[],
  values: FormValues
): Record<string, string> {
  const errors: Record<string, string> = {}
  for (const field of getVisibleFields(blocks, values)) {
    const value = values[field.name]
    const files =
      field.widget === "file" ? ((value ?? []) as FormFileItem[]) : []
    if (files.some((item) => item.status === "uploading")) {
      errors[field.name] = "Wait for the uploads to finish"
    } else if (files.some((item) => item.status === "failed")) {
      errors[field.name] = "Remove the files that failed to upload"
    } else if (field.required && isEmptyFormValue(value)) {
      errors[field.name] = "This field is required"
    } else if (field.widget === "number" && isUnsafeInteger(value)) {
      errors[field.name] =
        `Enter a whole number between -${MAX_SAFE} and ${MAX_SAFE}`
    }
  }
  return errors
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
