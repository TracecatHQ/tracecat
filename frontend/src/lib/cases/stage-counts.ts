import type { CaseSearchAggregationRead } from "@/client"

export interface CaseStageCounts {
  new: number
  in_progress: number
  on_hold: number
  resolved: number
  other: number
}

export const EMPTY_CASE_STAGE_COUNTS: CaseStageCounts = {
  new: 0,
  in_progress: 0,
  on_hold: 0,
  resolved: 0,
  other: 0,
}

function toFiniteNumber(value: unknown): number {
  if (typeof value === "number" && Number.isFinite(value)) {
    return value
  }
  if (typeof value === "string") {
    const parsed = Number(value)
    if (Number.isFinite(parsed)) {
      return parsed
    }
  }
  return 0
}

export function getCaseSearchTotal(
  aggregation: CaseSearchAggregationRead | null | undefined
): number | null {
  if (!aggregation || aggregation.agg !== "sum") {
    return null
  }
  if (typeof aggregation.value === "number") {
    return aggregation.value
  }
  if (!Array.isArray(aggregation.buckets)) {
    return null
  }
  const total = aggregation.buckets.reduce(
    (acc, bucket) => acc + toFiniteNumber(bucket.value),
    0
  )
  return total
}

export function getCaseStageCounts(
  aggregation: CaseSearchAggregationRead | null | undefined
): CaseStageCounts | null {
  if (!aggregation || aggregation.group_by !== "status") {
    return null
  }

  const counts: CaseStageCounts = { ...EMPTY_CASE_STAGE_COUNTS }
  for (const bucket of aggregation.buckets ?? []) {
    if (typeof bucket.group !== "string") {
      continue
    }
    const value = toFiniteNumber(bucket.value)
    switch (bucket.group) {
      case "new":
        counts.new += value
        break
      case "in_progress":
        counts.in_progress += value
        break
      case "on_hold":
        counts.on_hold += value
        break
      case "resolved":
      case "closed":
        counts.resolved += value
        break
      case "other":
      case "unknown":
        counts.other += value
        break
      default:
        break
    }
  }

  return counts
}
