import type {
  AgentFolderDirectoryItem,
  AgentPresetDirectoryItem,
  AgentPresetWorkflowReference,
} from "@/client"
import {
  type AgentCatalogFilters,
  groupWorkflowsByPreset,
  listReferencedWorkflows,
  matchesAgentCatalogFilters,
  parseAgentCatalogFilters,
  writeAgentCatalogFilters,
} from "@/lib/agent-catalog-filters"

const REFERENCES: AgentPresetWorkflowReference[] = [
  { preset_id: "triage", workflow_id: "wf_b", workflow_title: "Beta" },
  { preset_id: "triage", workflow_id: "wf_a", workflow_title: "Alpha" },
  { preset_id: "enrich", workflow_id: "wf_b", workflow_title: "Beta" },
  // Repeated by a later page of the scan.
  { preset_id: "triage", workflow_id: "wf_b", workflow_title: "Beta" },
]

function preset(id: string, mcpSlugs: string[]): AgentPresetDirectoryItem {
  return {
    type: "preset",
    id,
    name: id,
    slug: id,
    description: null,
    folder_id: null,
    tags: [],
    model_provider: "openai",
    model_name: "gpt-test",
    tool_summary: { tool_count: 0, namespaces: [], mcp_slugs: mcpSlugs },
    created_at: "2026-01-01T00:00:00Z",
    updated_at: "2026-01-01T00:00:00Z",
  }
}

const FOLDER = { type: "folder", id: "f" } as AgentFolderDirectoryItem

function filters(partial: Partial<AgentCatalogFilters>): AgentCatalogFilters {
  return { mcpSlugs: [], workflowIds: [], ...partial }
}

it("round-trips filters through URL params, keeping other params", () => {
  const params = writeAgentCatalogFilters(
    new URLSearchParams("view=list&mcp=stale"),
    filters({ mcpSlugs: ["github", "jira"], workflowIds: ["wf_a"] })
  )
  expect(params.toString()).toBe("view=list&mcp=github&mcp=jira&workflow=wf_a")
  expect(parseAgentCatalogFilters(params)).toEqual({
    mcpSlugs: ["github", "jira"],
    workflowIds: ["wf_a"],
  })
  expect(parseAgentCatalogFilters(null)).toEqual(filters({}))
})

it("groups workflows by preset and lists distinct workflows by title", () => {
  expect(groupWorkflowsByPreset(REFERENCES).get("triage")).toEqual([
    { id: "wf_a", title: "Alpha" },
    { id: "wf_b", title: "Beta" },
  ])
  expect(listReferencedWorkflows(groupWorkflowsByPreset(REFERENCES))).toEqual([
    { id: "wf_a", title: "Alpha" },
    { id: "wf_b", title: "Beta" },
  ])
})

it.each([
  ["no filters", filters({}), ["triage", "enrich", "plain", "f"]],
  [
    "any selected MCP",
    filters({ mcpSlugs: ["jira", "github"] }),
    ["triage", "enrich"],
  ],
  ["one MCP", filters({ mcpSlugs: ["jira"] }), ["enrich"]],
  ["a workflow", filters({ workflowIds: ["wf_a"] }), ["triage"]],
  [
    "MCP and workflow together",
    filters({ mcpSlugs: ["github"], workflowIds: ["wf_b"] }),
    ["triage"],
  ],
  ["an unused MCP", filters({ mcpSlugs: ["unused"] }), []],
])("matches %s", (_name, selected, expected) => {
  const byPreset = groupWorkflowsByPreset(REFERENCES)
  const items = [
    preset("triage", ["github"]),
    preset("enrich", ["jira"]),
    preset("plain", []),
    FOLDER,
  ]
  expect(
    items
      .filter((item) => matchesAgentCatalogFilters(item, selected, byPreset))
      .map((item) => item.id)
  ).toEqual(expected)
})
