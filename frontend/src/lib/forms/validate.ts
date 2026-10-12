import type { FormFieldBlock } from "@/client"
import {
  type FormBlock,
  type FormFileItem,
  type FormValues,
  getVisibleFields,
  isEmptyFormValue,
  isFieldBlock,
} from "@/lib/forms/visibility"

const ISO_DATE_PATTERN = /^(\d{4})-(\d{2})-(\d{2})$/

/** Whether a string is a real calendar date written as `YYYY-MM-DD`. */
export function isIsoDate(value: unknown): value is string {
  if (typeof value !== "string") {
    return false
  }
  const match = ISO_DATE_PATTERN.exec(value)
  if (!match) {
    return false
  }
  const [year, month, day] = match.slice(1).map(Number)
  const date = new Date(Date.UTC(year, month - 1, day))
  return (
    date.getUTCFullYear() === year &&
    date.getUTCMonth() === month - 1 &&
    date.getUTCDate() === day
  )
}

function isIsoDatetime(value: unknown): boolean {
  return typeof value === "string" && !Number.isNaN(Date.parse(value))
}

function getInitialValue(field: FormFieldBlock): unknown {
  switch (field.widget) {
    // A user or file default is an expanded object the client cannot send
    // back as an ID. Leaving the field empty lets the server apply it.
    case "user":
      return field.multiple ? [] : null
    case "file":
      return []
    case "toggle":
      return field.default ?? false
    case "text":
    case "long_text":
      return field.default ?? ""
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

function validateField(field: FormFieldBlock, value: unknown): string | null {
  if (field.widget === "file") {
    const items = (value ?? []) as FormFileItem[]
    if (items.some((item) => item.status === "uploading")) {
      return "Wait for the uploads to finish"
    }
    if (items.some((item) => item.status === "failed")) {
      return "Remove the files that failed to upload"
    }
  }
  if (isEmptyFormValue(value)) {
    return field.required ? "This field is required" : null
  }
  const items = Array.isArray(value) ? value : [value]
  if (field.options) {
    const allowed = new Set(field.options.map((option) => option.value))
    if (items.some((item) => typeof item !== "string" || !allowed.has(item))) {
      return "Choose one of the options"
    }
  }
  switch (field.widget) {
    case "number":
      if (typeof value !== "number" || !Number.isFinite(value)) {
        return "Enter a number"
      }
      return null
    case "date":
      return items.every(isIsoDate) ? null : "Enter a valid date"
    case "datetime":
      return items.every(isIsoDatetime) ? null : "Enter a valid date and time"
    default:
      return null
  }
}

/**
 * Check form state before it is submitted.
 *
 * Only visible fields are checked. The server repeats every check, so this
 * exists to give the requester an answer without a round trip.
 *
 * @returns One message per invalid field, keyed by field name.
 */
export function validateFormValues(
  blocks: FormBlock[],
  values: FormValues
): Record<string, string> {
  const errors: Record<string, string> = {}
  for (const field of getVisibleFields(blocks, values)) {
    const message = validateField(field, values[field.name])
    if (message) {
      errors[field.name] = message
    }
  }
  return errors
}
