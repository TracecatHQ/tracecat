"use client"

import type { CasesSearchCasesData } from "@/client"

/** Case ordering choices supported by the generated search contract. */
export type CaseSortField = NonNullable<CasesSearchCasesData["orderBy"]>

export interface CaseSortValue {
  field: CaseSortField
  direction: "asc" | "desc"
}

export const DEFAULT_CASE_SORT: CaseSortValue = {
  field: "updated_at",
  direction: "desc",
}
