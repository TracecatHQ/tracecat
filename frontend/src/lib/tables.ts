import fuzzysort from "fuzzysort"
import Papa from "papaparse"

export const SqlTypeEnum = [
  "TEXT",
  "INTEGER",
  "NUMERIC",
  "BOOLEAN",
  "DATE",
  "TIMESTAMP",
  "TIMESTAMPTZ",
  "JSONB",
  "SELECT",
  "MULTI_SELECT",
] as const

export const SqlTypeCreatableEnum = [
  "TEXT",
  "INTEGER",
  "NUMERIC",
  "BOOLEAN",
  "DATE",
  "TIMESTAMPTZ",
  "JSONB",
  "SELECT",
  "MULTI_SELECT",
] as const

export type SqlTypeCreatable = (typeof SqlTypeCreatableEnum)[number]

export interface CsvPreviewData {
  headers: string[]
  preview: Record<string, string>[]
}

const FUZZY_AUTO_MAP_SCORE_THRESHOLD = 0.82

interface TableColumnSearchTarget {
  name: string
  canonical: string
  search: string
}

export function canonicalizeColumnName(value: string): string {
  return value
    .replace(/^\uFEFF/, "")
    .trim()
    .toLowerCase()
    .replace(/[^a-z0-9]+/g, "_")
    .replace(/^_+|_+$/g, "")
}

function toFuzzySearchText(value: string): string {
  return canonicalizeColumnName(value).replace(/_/g, " ")
}

export function buildAutoColumnMapping(
  csvHeaders: string[],
  tableColumnNames: string[]
): Record<string, string> {
  const targets: TableColumnSearchTarget[] = tableColumnNames.map((name) => ({
    name,
    canonical: canonicalizeColumnName(name),
    search: toFuzzySearchText(name),
  }))
  const usedColumnNames = new Set<string>()
  const mapping: Record<string, string> = {}

  for (const header of csvHeaders) {
    let matchedName: string | null = null
    const canonicalHeader = canonicalizeColumnName(header)

    const exactMatch = targets.find(
      (target) => target.name === header && !usedColumnNames.has(target.name)
    )
    if (exactMatch) {
      matchedName = exactMatch.name
    }

    if (!matchedName) {
      const caseInsensitiveMatch = targets.find(
        (target) =>
          target.name.toLowerCase() === header.toLowerCase() &&
          !usedColumnNames.has(target.name)
      )
      if (caseInsensitiveMatch) {
        matchedName = caseInsensitiveMatch.name
      }
    }

    if (!matchedName && canonicalHeader) {
      const canonicalMatch = targets.find(
        (target) =>
          target.canonical === canonicalHeader &&
          !usedColumnNames.has(target.name)
      )
      if (canonicalMatch) {
        matchedName = canonicalMatch.name
      }
    }

    if (!matchedName) {
      const query = toFuzzySearchText(header)
      if (query) {
        const remainingTargets = targets.filter(
          (target) => !usedColumnNames.has(target.name)
        )
        const bestMatch = fuzzysort.go<TableColumnSearchTarget>(
          query,
          remainingTargets,
          {
            key: "search",
            limit: 1,
          }
        )[0]
        if (
          bestMatch &&
          bestMatch.score >= FUZZY_AUTO_MAP_SCORE_THRESHOLD &&
          !usedColumnNames.has(bestMatch.obj.name)
        ) {
          matchedName = bestMatch.obj.name
        }
      }
    }

    if (matchedName) {
      mapping[header] = matchedName
      usedColumnNames.add(matchedName)
    } else {
      mapping[header] = "skip"
    }
  }

  return mapping
}

export function resolveColumnMapping(
  csvHeaders: string[],
  tableColumnNames: string[],
  currentMapping: Record<string, string | undefined> = {}
): Record<string, string> {
  const validTargets = new Set<string>([...tableColumnNames, "skip"])
  const suggestedMapping = buildAutoColumnMapping(csvHeaders, tableColumnNames)
  const resolved: Record<string, string> = {}

  for (const header of csvHeaders) {
    const existing = currentMapping[header]
    if (typeof existing === "string" && validTargets.has(existing)) {
      resolved[header] = existing
      continue
    }
    resolved[header] = suggestedMapping[header] ?? "skip"
  }

  return resolved
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
        transformHeader: (header) => header.replace(/^\uFEFF/, ""),
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
