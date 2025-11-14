import type { LucideIcon } from "lucide-react"
import {
  Braces,
  CalendarClock,
  CircleDot,
  Hash,
  Tags,
  ToggleLeft,
  Type,
} from "lucide-react"
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
  TIMESTAMP: { label: "Date and time", icon: CalendarClock },
  TIMESTAMPTZ: { label: "Date and time", icon: CalendarClock },
  JSONB: { label: "JSON", icon: Braces },
  ENUM: { label: "Enum", icon: Tags },
}

export function getSqlTypeConfig(type?: SqlType | null) {
  if (!type) return undefined
  return SQL_TYPE_CONFIG[type]
}
