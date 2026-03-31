import type { LucideIcon } from "lucide-react"
import {
  Braces,
  Calendar,
  CalendarClock,
  CircleDot,
  Hash,
  Link,
  ListTodo,
  ScrollText,
  SquareCheck,
  ToggleLeft,
  Type,
} from "lucide-react"
import type { CaseFieldKind, CaseFieldReadType } from "@/client"
import type { SqlTypeEnum } from "@/lib/tables"

export type SqlType = (typeof SqlTypeEnum)[number]

export interface TypeConfig {
  label: string
  icon: LucideIcon
}

export const SQL_TYPE_CONFIG: Record<SqlType, TypeConfig> = {
  TEXT: { label: "Text", icon: Type },
  INTEGER: { label: "Integer", icon: Hash },
  NUMERIC: { label: "Number", icon: CircleDot },
  BOOLEAN: { label: "Boolean", icon: ToggleLeft },
  TIMESTAMPTZ: { label: "Date and time", icon: CalendarClock },
  DATE: { label: "Date", icon: Calendar },
  JSONB: { label: "JSON", icon: Braces },
  SELECT: { label: "Select", icon: SquareCheck },
  MULTI_SELECT: { label: "Multi-select", icon: ListTodo },
}

/** Display config overrides for case field kinds. */
export const CASE_FIELD_KIND_CONFIG: Record<CaseFieldKind, TypeConfig> = {
  LONG_TEXT: { label: "Long text", icon: ScrollText },
  URL: { label: "URL", icon: Link },
}

const CASE_FIELD_READ_TYPE_CONFIG: Record<CaseFieldReadType, TypeConfig> = {
  ...SQL_TYPE_CONFIG,
  UUID: { label: "UUID", icon: Hash },
}

/**
 * Get the display config for a field, preferring kind-specific config when available.
 */
export function getCaseFieldTypeConfig(
  type?: CaseFieldReadType | null,
  kind?: CaseFieldKind | null
): TypeConfig | undefined {
  if (kind) return CASE_FIELD_KIND_CONFIG[kind]
  if (!type) return undefined
  return CASE_FIELD_READ_TYPE_CONFIG[type]
}

export function getSqlTypeConfig(type?: SqlType | null) {
  if (!type) return undefined
  return SQL_TYPE_CONFIG[type]
}
