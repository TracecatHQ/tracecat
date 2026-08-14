import {
  Activity,
  AlignLeft,
  Braces,
  ListChecks,
  type LucideIcon,
  Paperclip,
  Table2,
} from "lucide-react"

/**
 * Ordered keys for the case panel switcher. `rows` is the Tables panel — the
 * value is live in shared case URLs and chat artifact URLs, so it stays `rows`.
 */
export const CASE_PANEL_KEYS = [
  "description",
  "tasks",
  "attachments",
  "rows",
  "payload",
  "activity",
] as const

/** One key from {@link CASE_PANEL_KEYS}. */
export type CasePanelKey = (typeof CASE_PANEL_KEYS)[number]

/** Panel shown when `?tab=` is absent, unrecognized, or entitlement-hidden. */
export const DEFAULT_CASE_PANEL: CasePanelKey = "description"

/** Retired `?tab=` values. `comments` was the old default. */
const CASE_PANEL_LEGACY_ALIASES: Readonly<
  Partial<Record<string, CasePanelKey>>
> = {
  comments: DEFAULT_CASE_PANEL,
}

/** Static definition of one switcher panel. */
export interface CasePanelDefinition {
  key: CasePanelKey
  label: string
  icon: LucideIcon
  /**
   * 1-based digit shortcut, fixed forever per panel — never derived from the
   * visible button list, so hiding a button never renumbers the others.
   */
  shortcut: number
}

/** Panel definitions in display order. */
export const CASE_PANELS: readonly CasePanelDefinition[] = [
  { key: "description", label: "Description", icon: AlignLeft, shortcut: 1 },
  { key: "tasks", label: "Tasks", icon: ListChecks, shortcut: 2 },
  { key: "attachments", label: "Attachments", icon: Paperclip, shortcut: 3 },
  { key: "rows", label: "Tables", icon: Table2, shortcut: 4 },
  { key: "payload", label: "Payload", icon: Braces, shortcut: 5 },
  { key: "activity", label: "Activity", icon: Activity, shortcut: 6 },
]

/**
 * Parses a raw `?tab=` value into a panel key, resolving legacy aliases.
 * Returns `null` when the value is missing or unrecognized.
 */
export function parseCasePanelKey(
  value: string | null | undefined
): CasePanelKey | null {
  if (!value) {
    return null
  }
  if ((CASE_PANEL_KEYS as readonly string[]).includes(value)) {
    return value as CasePanelKey
  }
  return CASE_PANEL_LEGACY_ALIASES[value] ?? null
}

/** DOM id for a panel's `role="tab"` button. */
export function casePanelTabId(idPrefix: string, key: CasePanelKey): string {
  return `${idPrefix}-tab-${key}`
}

/** DOM id for the `role="tabpanel"` container a tab controls. */
export function casePanelPanelId(idPrefix: string, key: CasePanelKey): string {
  return `${idPrefix}-panel-${key}`
}
