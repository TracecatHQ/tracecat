import fuzzysort from "fuzzysort"
import type { MCPIntegrationRead, RegistryActionReadMinimal } from "@/client"
import { isCustomRegistryOrigin } from "@/components/registry/utils"
import { isAgentToolSelectable } from "@/lib/agent-tools"

const SEARCH_KEYS = ["preparedTitle", "preparedKey"]

/** Form fields edited by the preset tools controls. */
export interface PresetToolFields {
  actions: string[]
  mcpIntegrations: string[]
  namespaces: string[]
  toolApprovals: Array<{ tool: string; allow: boolean }>
}

/** A searchable registry action or whole MCP integration. */
export interface ToolEntry {
  id: string
  key: string
  title: string
  description: string
  defaultAsk: boolean
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

/** Index selectable actions once, preserving source order independently of display order. */
export function buildToolIndex(
  actions: RegistryActionReadMinimal[] = [],
  integrations: MCPIntegrationRead[] = []
): ToolIndex {
  const entries: ToolEntry[] = []
  const groups = new Map<string, ToolGroup>()
  const byKey = new Map<string, ToolEntry>()
  function add(entry: ToolEntry, displayGroup?: string | null) {
    entries.push(entry)
    byKey.set(entry.id, entry)
    const id = `${entry.section}:${entry.namespace}`
    let group = groups.get(id)
    if (!group) {
      group = {
        id,
        namespace: entry.namespace,
        title: "",
        section: entry.section,
        entries: [],
      }
      groups.set(id, group)
    }
    if (!group.title && displayGroup) group.title = displayGroup
    group.entries.push(entry)
  }
  for (const action of actions) {
    if (!isAgentToolSelectable(action.action)) continue
    const title = action.default_title || action.name
    add(
      {
        id: action.action,
        key: action.action,
        title,
        description: action.description,
        defaultAsk: action.requires_approval ?? false,
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
        title: integration.name,
        description: integration.description ?? "",
        defaultAsk: false,
        namespace: integration.slug,
        section: "mcp",
        integration,
        preparedTitle: fuzzysort.prepare(integration.name),
        preparedKey: fuzzysort.prepare(integration.slug),
      },
      integration.name
    )
  }
  const sectionOrder = { mcp: 0, tracecat: 1, custom: 2 }
  const displayGroups = [...groups.values()]
  for (const group of displayGroups) {
    if (!group.title) {
      const words = group.namespace.replace(/[._-]/g, " ")
      group.title = words.charAt(0).toUpperCase() + words.slice(1)
    }
  }
  displayGroups.sort(
    (a, b) =>
      sectionOrder[a.section] - sectionOrder[b.section] ||
      a.title.localeCompare(b.title) ||
      a.id.localeCompare(b.id)
  )
  return { entries, groups: displayGroups, byKey }
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

/** Remove tools and their approval rules, retaining false rules that restate an auto default. */
export function removeTools(
  fields: Pick<PresetToolFields, "actions" | "toolApprovals">,
  removed: ReadonlySet<string>,
  defaultAskKeys: ReadonlySet<string> = new Set()
): Pick<PresetToolFields, "actions" | "toolApprovals"> {
  const actions = fields.actions.filter((key) => !removed.has(key))
  const rules = fields.toolApprovals.filter(
    (rule) =>
      !removed.has(rule.tool) || (!rule.allow && !defaultAskKeys.has(rule.tool))
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
