import { isMap, isSeq, parseDocument } from "yaml"
import type { MCPIntegrationRead, RegistryActionReadMinimal } from "@/client"
import { isAgentToolSelectable } from "@/lib/agent-tools"

/** Maximum number of tool declarations accepted by skill frontmatter. */
export const MAX_SKILL_TOOLS = 64

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

  const normalized = Array.from(
    new Set(values.map((value) => value.trim()).filter(Boolean))
  )
  if (normalized.length > MAX_SKILL_TOOLS) {
    return invalidToolsState(
      `metadata.tools supports at most ${MAX_SKILL_TOOLS} tool IDs.`
    )
  }

  return { valid: true, tools: normalized }
}

/**
 * Replace only `metadata.tools` while preserving unrelated YAML keys and
 * comments held by the parsed YAML document.
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
  document.setIn(["metadata", "tools"], normalized)

  const serialized = document.toString().replace(/\n$/, "")
  return frontmatter.includes("\r\n")
    ? serialized.replace(/\n/g, "\r\n")
    : serialized
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

  const mcpOptions = mcpIntegrations.flatMap<SkillToolOption>((integration) => {
    const integrationOption: SkillToolOption = {
      value: `mcp.${integration.slug}`,
      label: "All tools",
      description:
        integration.description || `Allow every tool from ${integration.name}.`,
      group: integration.name,
      kind: "mcp-integration",
      tagLabel: "All tools",
      tagGroup: integration.name,
    }
    const toolOptions = (integration.tools ?? [])
      .filter((tool) => tool.enabled !== false && tool.status !== "missing")
      .map<SkillToolOption>((tool) => ({
        value: `mcp.${integration.slug}.${tool.name}`,
        label: tool.name,
        description: tool.description || undefined,
        group: integration.name,
        kind: "mcp-tool",
        tagLabel: tool.name,
        tagGroup: integration.name,
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
