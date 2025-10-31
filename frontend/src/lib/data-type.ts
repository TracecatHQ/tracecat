import type { LucideIcon } from "lucide-react"
import {
  Braces,
  Calendar,
  CalendarClock,
  CircleDot,
  Hash,
  ListTodo,
  SquareCheck,
  ToggleLeft,
  Type,
} from "lucide-react"
import type { FieldType } from "@/client"
import type { SqlTypeEnum } from "@/lib/tables"

export type SqlType = (typeof SqlTypeEnum)[number]

export interface TypeConfig {
  label: string
  icon: LucideIcon
}

export const FIELD_TYPE_CONFIG: Record<FieldType, TypeConfig> = {
  TEXT: { label: "Text", icon: Type },
  INTEGER: { label: "Integer", icon: Hash },
  NUMBER: { label: "Number", icon: CircleDot },
  BOOL: { label: "Boolean", icon: ToggleLeft },
  JSON: { label: "JSON", icon: Braces },
  DATE: { label: "Date", icon: Calendar },
  DATETIME: { label: "Date and time", icon: CalendarClock },
  SELECT: { label: "Select", icon: SquareCheck },
  MULTI_SELECT: { label: "Multi-select", icon: ListTodo },
}

export const SQL_TYPE_CONFIG: Record<SqlType, TypeConfig> = {
  TEXT: { label: "Text", icon: Type },
  INTEGER: { label: "Integer", icon: Hash },
  NUMERIC: { label: "Number", icon: CircleDot },
  BOOLEAN: { label: "Boolean", icon: ToggleLeft },
  TIMESTAMP: { label: "Date and time", icon: CalendarClock },
  TIMESTAMPTZ: { label: "Date and time", icon: CalendarClock },
  JSONB: { label: "JSON", icon: Braces },
}

export function getFieldTypeConfig(type?: FieldType | null) {
  if (!type) return undefined
  return FIELD_TYPE_CONFIG[type]
}

export function getSqlTypeConfig(type?: SqlType | null) {
  if (!type) return undefined
  return SQL_TYPE_CONFIG[type]
}
