import fuzzysort from "fuzzysort"
import type { MCPIntegrationRead, RegistryActionReadMinimal } from "@/client"
import { isCustomRegistryOrigin } from "@/components/registry/utils"
import { isAgentToolSelectable } from "@/lib/agent-tools"

const SEARCH_KEYS = ["preparedTitle", "preparedKey"]

/** Controlled value shared by the tools list and picker. */
export interface ToolSelectionValue {
  actions: string[]
  mcpIntegrations: string[]
  /** Stored namespace filter; omit when the surface has none. */
  namespaces?: string[]
  /** Approval overrides; omit when the surface has no approvals. */
  toolApprovals?: Array<{ tool: string; allow: boolean }>
}

/** Preset form fields: the selection value with every field present. */
export type PresetToolFields = Required<ToolSelectionValue>

/**
 * A selectable tool: a registry action, a whole MCP integration, or one MCP tool.
 *
 * `id` is unique in the index. Entries written to `actions` use their key as id.
 */
export interface ToolEntry {
  id: string
  key: string
  /** Value field that stores `key` when the entry is selected. */
  target: "actions" | "mcpIntegrations"
  title: string
  description: string
  defaultAsk: boolean
  locked: boolean
  namespace: string
  section: "mcp" | "tracecat" | "custom"
  preparedTitle: Fuzzysort.Prepared
  preparedKey: Fuzzysort.Prepared
  integration?: MCPIntegrationRead
}

/** A display group; its entry order follows the registry response. */
export interface ToolGroup {
  id: string
  namespace: string
  title: string
  section: ToolEntry["section"]
  entries: ToolEntry[]
}

/** Prepared search data and lookup tables shared by the list and picker. */
export interface ToolIndex {
  entries: ToolEntry[]
  groups: ToolGroup[]
  byKey: Map<string, ToolEntry>
}

/** Build the group id shared by every entry in a section and namespace. */
export function getToolGroupId(
  entry: Pick<ToolEntry, "section" | "namespace">
): string {
  return `${entry.section}:${entry.namespace}`
}

/** Group prebuilt entries into an index; `groupTitles` is keyed by group id. */
export function createToolIndex(
  entries: ToolEntry[],
  groupTitles: ReadonlyMap<string, string> = new Map()
): ToolIndex {
  const groups = new Map<string, ToolGroup>()
  const byKey = new Map<string, ToolEntry>()
  for (const entry of entries) {
    byKey.set(entry.id, entry)
    const id = getToolGroupId(entry)
    let group = groups.get(id)
    if (!group) {
      const words = entry.namespace.replace(/[._-]/g, " ")
      group = {
        id,
        namespace: entry.namespace,
        title:
          groupTitles.get(id) ?? words.charAt(0).toUpperCase() + words.slice(1),
        section: entry.section,
        entries: [],
      }
      groups.set(id, group)
    }
    group.entries.push(entry)
  }
  const sectionOrder = { mcp: 0, tracecat: 1, custom: 2 }
  const displayGroups = [...groups.values()].sort(
    (a, b) =>
      sectionOrder[a.section] - sectionOrder[b.section] ||
      a.title.localeCompare(b.title) ||
      a.id.localeCompare(b.id)
  )
  return { entries, groups: displayGroups, byKey }
}

/** Index selectable actions once, preserving source order independently of display order. */
export function buildToolIndex(
  actions: RegistryActionReadMinimal[] = [],
  integrations: MCPIntegrationRead[] = [],
  options: { filterAgentTools?: boolean } = {}
): ToolIndex {
  const entries: ToolEntry[] = []
  const groupTitles = new Map<string, string>()
  function add(entry: ToolEntry, displayGroup?: string | null) {
    entries.push(entry)
    const id = getToolGroupId(entry)
    if (displayGroup && !groupTitles.has(id)) groupTitles.set(id, displayGroup)
  }
  const filterAgentTools = options.filterAgentTools ?? true
  for (const action of actions) {
    if (filterAgentTools && !isAgentToolSelectable(action.action)) continue
    const title = action.default_title || action.name
    add(
      {
        id: action.action,
        key: action.action,
        target: "actions",
        title,
        description: action.description,
        defaultAsk: action.requires_approval ?? false,
        locked: action.availability?.locked ?? false,
        namespace: action.namespace,
        section: isCustomRegistryOrigin(action.origin) ? "custom" : "tracecat",
        preparedTitle: fuzzysort.prepare(title),
        preparedKey: fuzzysort.prepare(action.action),
      },
      action.display_group
    )
  }
  for (const integration of integrations) {
    add(
      {
        id: `mcp:${integration.id}`,
        key: integration.id,
        target: "mcpIntegrations",
        title: integration.name,
        description: integration.description ?? "",
        defaultAsk: false,
        locked: false,
        namespace: integration.slug,
        section: "mcp",
        integration,
        preparedTitle: fuzzysort.prepare(integration.name),
        preparedKey: fuzzysort.prepare(integration.slug),
      },
      integration.name
    )
  }
  return createToolIndex(entries, groupTitles)
}

/** Search prepared titles and keys without mounting the catalog. */
export function searchTools(index: ToolIndex, query: string): ToolEntry[] {
  return fuzzysort
    .go(query, index.entries, { keys: SEARCH_KEYS, limit: 200 })
    .map((result) => result.obj)
}

/** Preserve existing order and append additions in index order; reuse unchanged arrays. */
export function applyToolSelection(
  current: string[],
  next: ReadonlySet<string>,
  indexOrder: string[]
): string[] {
  const existing = new Set(current)
  const result = current.filter((key) => next.has(key))
  for (const key of indexOrder) {
    if (next.has(key) && !existing.has(key)) {
      result.push(key)
      existing.add(key)
    }
  }
  return result.length === current.length &&
    result.every((key, i) => key === current[i])
    ? current
    : result
}

/** Resolve a stored override before falling back to the action's approval default. */
export function getToolApproval(
  rules: PresetToolFields["toolApprovals"],
  key: string,
  index: ToolIndex
): boolean {
  return (
    rules.find((rule) => rule.tool === key)?.allow ??
    index.byKey.get(key)?.defaultAsk ??
    false
  )
}

/** Set an explicit approval choice without rewriting unrelated entries. */
export function setToolApproval(
  rules: PresetToolFields["toolApprovals"],
  key: string,
  ask: boolean,
  defaultAsk = false
): PresetToolFields["toolApprovals"] {
  const index = rules.findIndex((rule) => rule.tool === key)
  if (ask === defaultAsk) {
    const next = rules.filter(
      (rule) => rule.tool !== key || rule.allow === defaultAsk
    )
    return next.length === rules.length ? rules : next
  }
  if (index < 0) return [...rules, { tool: key, allow: ask }]
  if (rules[index].allow === ask) return rules
  return rules.map((rule, i) => (i === index ? { ...rule, allow: ask } : rule))
}

/** Remove tools, keeping rules for retained grants or false rules that restate an auto default. */
export function removeTools(
  fields: Pick<PresetToolFields, "actions" | "toolApprovals">,
  removed: ReadonlySet<string>,
  defaultAskKeys: ReadonlySet<string> = new Set(),
  retainedKeys: ReadonlySet<string> = new Set()
): Pick<PresetToolFields, "actions" | "toolApprovals"> {
  const actions = fields.actions.filter((key) => !removed.has(key))
  const rules = fields.toolApprovals.filter(
    (rule) =>
      !removed.has(rule.tool) ||
      retainedKeys.has(rule.tool) ||
      (!rule.allow && !defaultAskKeys.has(rule.tool))
  )
  return {
    actions:
      actions.length === fields.actions.length ? fields.actions : actions,
    toolApprovals:
      rules.length === fields.toolApprovals.length
        ? fields.toolApprovals
        : rules,
  }
}

/** Namespace prefixes filter existing grants; they never grant additional tools. */
export function getBlockedActions(
  actions: string[],
  namespaces: string[]
): string[] {
  if (namespaces.length === 0) return []
  return actions.filter(
    (key) => !namespaces.some((prefix) => key.startsWith(prefix))
  )
}
