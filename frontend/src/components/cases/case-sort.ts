"use client"

export type CaseSortField =
  | "updated_at"
  | "created_at"
  | "priority"
  | "severity"
  | "status"
  | "tasks"

export interface CaseSortValue {
  field: CaseSortField
  direction: "asc" | "desc"
}

export const DEFAULT_CASE_SORT: CaseSortValue = {
  field: "updated_at",
  direction: "desc",
}
