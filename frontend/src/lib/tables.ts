import Papa from "papaparse"

export const SqlTypeEnum = [
  "TEXT",
  "INTEGER",
  "NUMERIC",
  "BOOLEAN",
  "TIMESTAMP",
  "TIMESTAMPTZ",
  "JSONB",
] as const

export const SqlTypeCreatableEnum = [
  "TEXT",
  "INTEGER",
  "NUMERIC",
  "BOOLEAN",
  "TIMESTAMPTZ",
  "JSONB",
] as const

export type SqlTypeCreatable = (typeof SqlTypeCreatableEnum)[number]

export interface CsvPreviewData {
  headers: string[]
  preview: Record<string, string>[]
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
