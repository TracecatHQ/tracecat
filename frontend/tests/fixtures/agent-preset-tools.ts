import fuzzysort from "fuzzysort"
import type { MCPIntegrationRead, RegistryActionReadMinimal } from "@/client"
import {
  buildToolIndex,
  createToolIndex,
  type ToolEntry,
  type ToolIndex,
} from "@/lib/agent-preset-tools"

/** Build synthetic registry entries for preset tool tests. */
export function registryTool(
  key: string,
  overrides: Partial<RegistryActionReadMinimal> = {}
): RegistryActionReadMinimal {
  const parts = key.split(".")
  const name = parts.pop() ?? key
  return {
    id: key,
    action: key,
    namespace: parts.join("."),
    name,
    description: `Description of ${name}`,
    default_title: name.replaceAll("_", " "),
    display_group: null,
    origin: "tracecat_registry",
    type: "udf",
    ...overrides,
  }
}

/** Build a whole MCP integration using the generated API contract. */
export function mcpIntegration(
  overrides: Partial<MCPIntegrationRead> = {}
): MCPIntegrationRead {
  return {
    id: "mcp-test",
    workspace_id: "workspace-test",
    name: "Test MCP",
    slug: "test-mcp",
    description: null,
    server_type: "http",
    server_uri: "https://example.com/mcp",
    auth_type: "NONE",
    oauth_integration_id: null,
    state: "connected",
    stdio_command: null,
    stdio_args: null,
    timeout: null,
    created_at: "2026-01-01T00:00:00Z",
    updated_at: "2026-01-01T00:00:00Z",
    ...overrides,
  }
}

/** A catalog large enough to exercise both virtualized axes and group boundaries. */
export function largeToolCatalog(): RegistryActionReadMinimal[] {
  return Array.from({ length: 1500 }, (_, i) =>
    registryTool(
      `tools.vendor_${String(Math.floor(i / 12)).padStart(3, "0")}.action_${i}`
    )
  )
}

/** Build one hand-made MCP entry stored in `actions`, as a flat id list needs. */
export function mcpToolEntry(key: string, title: string): ToolEntry {
  return {
    id: key,
    key,
    target: "actions",
    title,
    description: `Description of ${title}`,
    defaultAsk: false,
    locked: false,
    namespace: "test-mcp",
    section: "mcp",
    preparedTitle: fuzzysort.prepare(title),
    preparedKey: fuzzysort.prepare(key),
  }
}

/** A caller-built index: one MCP server granted whole or per tool, plus a registry action. */
export function flatIdToolIndex(): ToolIndex {
  return createToolIndex(
    [
      mcpToolEntry("mcp.test-mcp", "Whole server"),
      mcpToolEntry("mcp.test-mcp.read", "Read"),
      mcpToolEntry("mcp.test-mcp.write", "Write"),
      ...buildToolIndex([registryTool("tools.test.first")]).entries,
    ],
    new Map([["mcp:test-mcp", "Test MCP"]])
  )
}
