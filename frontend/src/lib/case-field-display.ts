import type { CaseFieldReadMinimal, CaseFieldReadType } from "@/client"
import { slugify } from "@/lib/utils"

export const MAX_CASE_FIELD_FRACTION_DIGITS = 6
export const MAX_CASE_FIELD_REFERENCE_LENGTH = 100

/**
 * Derive the snake_case API reference used to store and address a case field.
 */
export function createCaseFieldReference(displayName: string): string {
  const normalized = slugify(displayName, "_")
  const validStart = /^\d/.test(normalized) ? `field_${normalized}` : normalized
  return validStart.slice(0, MAX_CASE_FIELD_REFERENCE_LENGTH)
}

/**
 * Derive the first available case-field reference, suffixing collisions.
 */
export function createUniqueCaseFieldReference(
  displayName: string,
  existingReferences: ReadonlySet<string>
): string {
  const baseReference = createCaseFieldReference(displayName)
  if (!baseReference || !existingReferences.has(baseReference)) {
    return baseReference
  }

  let counter = 2
  while (true) {
    const suffix = `_${counter}`
    const candidate = `${baseReference.slice(
      0,
      MAX_CASE_FIELD_REFERENCE_LENGTH - suffix.length
    )}${suffix}`
    if (!existingReferences.has(candidate)) {
      return candidate
    }
    counter += 1
  }
}

/**
 * Index case-field display names by their stable API references.
 */
export function createCaseFieldDisplayNameMap(
  fields: readonly Pick<CaseFieldReadMinimal, "id" | "display_name">[] = []
): ReadonlyMap<string, string> {
  return new Map(fields.map((field) => [field.id, field.display_name]))
}

/**
 * Round numeric case-field values for display without exposing float artifacts.
 */
export function formatCaseFieldNumericDisplayValue(
  value: unknown,
  maximumFractionDigits = MAX_CASE_FIELD_FRACTION_DIGITS
): string | null {
  const preservedValue =
    typeof value === "string"
      ? preserveExactNumericDisplay(value, maximumFractionDigits)
      : null
  if (preservedValue) {
    return preservedValue
  }

  const parsed = parseFiniteCaseFieldNumber(value)
  if (parsed === null) {
    return null
  }

  return new Intl.NumberFormat(undefined, {
    useGrouping: false,
    maximumFractionDigits,
  }).format(parsed)
}

/**
 * Normalize editable numeric field values so inputs start from an unrounded raw string.
 */
export function getCaseFieldEditorValue(
  value: unknown,
  fieldType: CaseFieldReadType
): unknown {
  if (fieldType === "NUMERIC") {
    return getCaseFieldNumericEditorValue(value) ?? value
  }

  if (fieldType === "INTEGER") {
    return getCaseFieldIntegerEditorValue(value) ?? value
  }

  return value
}

/**
 * Format a case-field value for badges and read-only labels.
 */
export function formatCaseFieldDisplayLabel(
  value: unknown,
  fieldType?: CaseFieldReadType
): string {
  if (typeof value === "boolean") {
    return value ? "Yes" : "No"
  }

  if (fieldType === "NUMERIC") {
    return formatCaseFieldNumericDisplayValue(value) ?? String(value)
  }

  if (fieldType === "INTEGER") {
    const parsed = parseFiniteCaseFieldNumber(value)
    if (parsed !== null && Number.isInteger(parsed)) {
      return String(parsed)
    }
    return String(value)
  }

  if (typeof value === "number") {
    return formatCaseFieldNumericDisplayValue(value) ?? String(value)
  }

  if (typeof value === "object" && value !== null) {
    const obj = value as Record<string, unknown>
    return typeof obj.label === "string" ? obj.label : JSON.stringify(value)
  }

  return String(value)
}

/**
 * Return true when a custom case-field value should be treated as empty.
 * Booleans are never empty (`false` is a real answer); strings are empty when
 * blank or whitespace-only; arrays and objects when they have no entries.
 */
export function isCustomFieldValueEmpty(value: unknown): boolean {
  if (value === null || value === undefined) return true
  if (typeof value === "string") return value.trim().length === 0
  if (typeof value === "number") return Number.isNaN(value)
  if (typeof value === "boolean") return false
  if (Array.isArray(value)) return value.length === 0
  if (typeof value === "object")
    return Object.keys(value as object).length === 0
  return false
}

/**
 * Order custom case fields for the case panel. When collapsed (`showAll` is
 * false), only non-empty fields are shown. When expanded, non-empty fields
 * come first and empty fields follow, each group keeping its original
 * relative order — a stable partition, not a sort.
 */
export function orderCustomFieldsForDisplay<T extends { value: unknown }>(
  fields: T[],
  showAll: boolean
): T[] {
  const nonEmpty = fields.filter(
    (field) => !isCustomFieldValueEmpty(field.value)
  )
  if (!showAll) {
    return nonEmpty
  }
  const empty = fields.filter((field) => isCustomFieldValueEmpty(field.value))
  return [...nonEmpty, ...empty]
}

function parseFiniteCaseFieldNumber(value: unknown): number | null {
  if (typeof value === "number") {
    return Number.isFinite(value) ? value : null
  }

  if (typeof value !== "string") {
    return null
  }

  const trimmed = value.trim()
  if (!trimmed) {
    return null
  }

  const parsed = Number(trimmed)
  return Number.isFinite(parsed) ? parsed : null
}

function getCaseFieldNumericEditorValue(value: unknown): string | null {
  if (typeof value === "number") {
    return Number.isFinite(value) ? String(value) : null
  }

  if (typeof value !== "string") {
    return null
  }

  const trimmed = value.trim()
  return trimmed && Number.isFinite(Number(trimmed)) ? trimmed : null
}

function getCaseFieldIntegerEditorValue(value: unknown): string | null {
  if (typeof value === "number") {
    return Number.isInteger(value) ? String(value) : null
  }

  if (typeof value !== "string") {
    return null
  }

  const trimmed = value.trim()
  return /^[-+]?\d+$/.test(trimmed) ? trimmed : null
}

function preserveExactNumericDisplay(
  value: string,
  maximumFractionDigits: number
): string | null {
  const trimmed = value.trim()
  if (!trimmed) {
    return null
  }

  const match = trimmed.match(/^([+-]?)(\d*)(?:\.(\d+))?$/)
  if (!match) {
    return null
  }

  const fractionDigits = match[3]?.length ?? 0
  if (fractionDigits > maximumFractionDigits) {
    return null
  }

  if (!Number.isFinite(Number(trimmed))) {
    return null
  }

  if (trimmed.startsWith(".")) {
    return `0${trimmed}`
  }
  if (trimmed.startsWith("-.")) {
    return trimmed.replace("-.", "-0.")
  }
  if (trimmed.startsWith("+.")) {
    return trimmed.replace("+.", "0.")
  }

  return trimmed.startsWith("+") ? trimmed.slice(1) : trimmed
}
