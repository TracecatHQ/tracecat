import type { CaseBatchResponse } from "@/client"

// Keep aligned with the server's batch request cap.
const CASE_BATCH_MAX_IDS = 1000

/** Split case IDs into requests that satisfy the server batch-size contract. */
export function chunkCaseIds(caseIds: string[]): string[][] {
  const chunks: string[][] = []
  for (let start = 0; start < caseIds.length; start += CASE_BATCH_MAX_IDS) {
    chunks.push(caseIds.slice(start, start + CASE_BATCH_MAX_IDS))
  }
  return chunks
}

/** Aggregated per-case outcome of a chunked batch operation. */
export interface ChunkedCaseBatchResult {
  succeededIds: Set<string>
  failed: number
  /** Per-case error messages returned by the server. */
  errors: string[]
  /** Set when a request threw, which stops the remaining chunks. */
  error?: unknown
}

/**
 * Run a batch case operation in server-sized chunks and collect per-case
 * results. A thrown request stops the run and is returned in `error`, so
 * callers can still tell which cases already succeeded.
 */
export async function runChunkedCaseBatch(
  caseIds: string[],
  request: (chunk: string[]) => Promise<CaseBatchResponse>
): Promise<ChunkedCaseBatchResult> {
  const result: ChunkedCaseBatchResult = {
    succeededIds: new Set(),
    failed: 0,
    errors: [],
  }
  try {
    for (const chunk of chunkCaseIds(caseIds)) {
      const response = await request(chunk)
      for (const item of response.results) {
        if (item.success) {
          result.succeededIds.add(item.case_id)
        } else if (item.error) {
          result.errors.push(item.error)
        }
      }
      result.failed += response.failed
    }
  } catch (error) {
    result.error = error
  }
  return result
}
