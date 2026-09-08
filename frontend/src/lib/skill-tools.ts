import { isMap, isSeq, parseDocument, type YAMLMap } from "yaml"
import type { MCPIntegrationRead, RegistryActionReadMinimal } from "@/client"
import { isAgentToolSelectable } from "@/lib/agent-tools"

/** Maximum number of tool declarations accepted by skill frontmatter. */
export const MAX_SKILL_TOOLS = 64

// Keep in sync with ToolId in tracecat/agent/skill/frontmatter.py.
const MCP_TOOL_ID_RE = /^mcp\.[a-z0-9_-]+(?:\.[A-Za-z0-9_-]+)?$/
const REGISTRY_TOOL_ID_RE = /^[a-z0-9_]+(?:\.[a-z0-9_]+)+$/

function isCanonicalToolId(value: string): boolean {
  const pattern = value.startsWith("mcp.")
    ? MCP_TOOL_ID_RE
    : REGISTRY_TOOL_ID_RE
  return value.length >= 3 && value.length <= 255 && pattern.test(value)
}

/** Tool option shown in the Skills Studio frontmatter picker. */
export interface SkillToolOption {
  value: string
  label: string
  description?: string
  group: string
  kind: "registry" | "mcp-integration" | "mcp-tool"
  tagLabel?: string
  tagGroup?: string
}

/** Parsed `metadata.tools` state from raw skill frontmatter YAML. */
export type SkillFrontmatterToolsState =
  | { valid: true; tools: string[] }
  | { valid: false; message: string; tools: [] }

/**
 * Read tool declarations without changing the user's raw frontmatter YAML.
 */
export function readSkillFrontmatterTools(
  frontmatter: string
): SkillFrontmatterToolsState {
  const document = parseDocument(frontmatter, { keepSourceTokens: true })
  if (document.errors.length > 0 || !isMap(document.contents)) {
    return invalidToolsState("Fix the frontmatter YAML to edit tools here.")
  }

  const metadata = document.get("metadata", true)
  if (document.contents.has("<<") || (isMap(metadata) && metadata.has("<<"))) {
    return invalidToolsState(
      "Edit tools in the YAML editor when metadata uses merge keys."
    )
  }
  if (metadata === undefined || metadata === null) {
    return { valid: true, tools: [] }
  }
  if (!isMap(metadata)) {
    return invalidToolsState("metadata must be a YAML mapping.")
  }

  const tools = metadata.get("tools", true)
  if (tools === undefined || tools === null) {
    return { valid: true, tools: [] }
  }
  if (!isSeq(tools)) {
    return invalidToolsState("metadata.tools must be a YAML list.")
  }

  const values = tools.toJSON()
  if (
    !Array.isArray(values) ||
    values.some((value) => typeof value !== "string")
  ) {
    return invalidToolsState("metadata.tools must contain only tool IDs.")
  }

  if (values.some((value) => value.trim().length === 0)) {
    return invalidToolsState("metadata.tools must not contain blank tool IDs.")
  }
  if (values.length > MAX_SKILL_TOOLS) {
    return invalidToolsState(
      `metadata.tools supports at most ${MAX_SKILL_TOOLS} tool IDs.`
    )
  }

  return {
    valid: true,
    tools: Array.from(new Set(values.map((value) => value.trim()))),
  }
}

/**
 * Replace only `metadata.tools` while preserving unrelated YAML source.
 */
export function updateSkillFrontmatterTools(
  frontmatter: string,
  tools: string[]
): string {
  const state = readSkillFrontmatterTools(frontmatter)
  if (!state.valid) {
    throw new Error(state.message)
  }

  const normalized = Array.from(
    new Set(tools.map((tool) => tool.trim()).filter(Boolean))
  )
  if (normalized.length > MAX_SKILL_TOOLS) {
    throw new Error(`Skills support at most ${MAX_SKILL_TOOLS} tools.`)
  }

  const document = parseDocument(frontmatter, { keepSourceTokens: true })
  const existing = document.getIn(["metadata", "tools"], true)
  const serialized = JSON.stringify(normalized)
  const newline = frontmatter.includes("\r\n") ? "\r\n" : "\n"
  if (isSeq(existing) && existing.range) {
    // Node ranges exclude the sequence's anchor. Keep it and all source
    // outside the tools value verbatim, including YAML 1.1 scalar spellings.
    const [start, end] = existing.range
    const suffix = frontmatter.slice(start, end).endsWith("\n") ? newline : ""
    return (
      frontmatter.slice(0, start) + serialized + suffix + frontmatter.slice(end)
    )
  }

  const metadata = document.get("metadata", true)
  if (isMap(metadata)) {
    return insertMappingEntry(
      frontmatter,
      metadata,
      `tools: ${serialized}`,
      newline
    )
  }
  if (isMap(document.contents)) {
    return insertMappingEntry(
      frontmatter,
      document.contents,
      `metadata: { tools: ${serialized} }`,
      newline
    )
  }
  throw new Error("Frontmatter must be a YAML mapping.")
}

/** Insert a new key without serializing any existing YAML nodes. */
function insertMappingEntry(
  source: string,
  mapping: YAMLMap,
  entry: string,
  newline: string
): string {
  if (!mapping.range) {
    throw new Error("Cannot locate the YAML mapping in the source.")
  }
  const start = mapping.range[0]
  if (mapping.flow) {
    const separator = mapping.items.length > 0 ? ", " : ""
    return (
      source.slice(0, start + 1) + entry + separator + source.slice(start + 1)
    )
  }
  const token = mapping.srcToken
  const indent = " ".repeat(token && "indent" in token ? token.indent : 0)
  return source.slice(0, start) + entry + newline + indent + source.slice(start)
}

/**
 * Build canonical registry and MCP tool options for the frontmatter picker.
 */
export function buildSkillToolOptions(
  registryActions: RegistryActionReadMinimal[],
  mcpIntegrations: MCPIntegrationRead[]
): SkillToolOption[] {
  const registryOptions = registryActions
    .filter((action) => isAgentToolSelectable(action.action))
    .map<SkillToolOption>((action) => ({
      value: action.action,
      label: action.default_title || action.action,
      description: action.description,
      group: action.display_group || action.namespace,
      kind: "registry",
      tagLabel: action.default_title || action.name,
      tagGroup: action.display_group || action.namespace,
    }))

  const nameCounts = new Map<string, number>()
  for (const integration of mcpIntegrations) {
    nameCounts.set(
      integration.name,
      (nameCounts.get(integration.name) ?? 0) + 1
    )
  }
  const mcpOptions = mcpIntegrations.flatMap<SkillToolOption>((integration) => {
    const integrationLabel =
      (nameCounts.get(integration.name) ?? 0) > 1
        ? `${integration.name} (${integration.slug})`
        : integration.name
    const integrationOption: SkillToolOption = {
      value: `mcp.${integration.slug}`,
      label: "All tools",
      description:
        integration.description || `Allow every tool from ${integration.name}.`,
      group: integrationLabel,
      kind: "mcp-integration",
      tagLabel: "All tools",
      tagGroup: integrationLabel,
    }
    if (integration.server_type === "stdio") {
      return [integrationOption]
    }
    const toolOptions = (integration.tools ?? [])
      .filter(
        (tool) =>
          tool.enabled !== false &&
          tool.status !== "missing" &&
          isCanonicalToolId(`mcp.${integration.slug}.${tool.name}`)
      )
      .map<SkillToolOption>((tool) => ({
        value: `mcp.${integration.slug}.${tool.name}`,
        label: tool.name,
        description: tool.description || undefined,
        group: integrationLabel,
        kind: "mcp-tool",
        tagLabel: tool.name,
        tagGroup: integrationLabel,
      }))

    return [integrationOption, ...toolOptions]
  })

  return [...registryOptions, ...mcpOptions].sort((left, right) =>
    left.value.localeCompare(right.value)
  )
}

function invalidToolsState(message: string): SkillFrontmatterToolsState {
  return { valid: false, message, tools: [] }
}
