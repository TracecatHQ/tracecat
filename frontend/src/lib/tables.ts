import Papa from "papaparse"
import type { TableColumnRead } from "@/client"

export const SqlTypeEnum = [
  "TEXT",
  "INTEGER",
  "BIGINT",
  "NUMERIC",
  "BOOLEAN",
  "TIMESTAMP",
  "TIMESTAMPTZ",
  "JSONB",
  "ENUM",
] as const

export const SqlTypeCreatableEnum = [
  "TEXT",
  "INTEGER",
  "NUMERIC",
  "BOOLEAN",
  "TIMESTAMPTZ",
  "JSONB",
  "ENUM",
] as const

export type SqlTypeCreatable = (typeof SqlTypeCreatableEnum)[number]

export interface CsvPreviewData {
  headers: string[]
  preview: Record<string, string>[]
}

export function parseEnumValuesInput(raw?: string | string[] | null): string[] {
  if (!raw) return []

  const values = Array.isArray(raw) ? raw : raw.split(/\r?\n|,/) // support legacy textarea input

  const seen = new Set<string>()
  const cleaned: string[] = []

  for (const value of values) {
    const trimmed = value.trim()
    if (!trimmed || seen.has(trimmed)) continue
    cleaned.push(trimmed)
    seen.add(trimmed)
  }

  return cleaned
}

export function getColumnEnumValues(column: TableColumnRead): string[] {
  const raw = column.default
  if (!raw || typeof raw !== "object") {
    return []
  }

  const metadata = raw as Record<string, unknown>
  const source =
    metadata.enum_values ??
    metadata.values ??
    metadata.options ??
    (Array.isArray(metadata) ? metadata : undefined)

  if (!Array.isArray(source)) return []

  const seen = new Set<string>()
  const output: string[] = []
  for (const item of source) {
    if (typeof item !== "string") continue
    const normalised = item.trim()
    if (!normalised || seen.has(normalised)) continue
    seen.add(normalised)
    output.push(normalised)
  }
  return output
}

export async function getCsvPreview(
  file: File,
  nRows: number = 5
): Promise<CsvPreviewData> {
  const results = await new Promise<Papa.ParseResult<Record<string, string>>>(
    (resolve, reject) => {
      Papa.parse<Record<string, string>>(file, {
        header: true,
        skipEmptyLines: true,
        complete: (results) => resolve(results),
        error: (error) => reject(error),
        preview: nRows,
      })
    }
  )

  const headers = results.meta.fields || []
  return {
    headers,
    preview: results.data,
  }
}
