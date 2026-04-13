import type { CaseFieldReadType } from "@/client"

export const MAX_CASE_FIELD_FRACTION_DIGITS = 6

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
