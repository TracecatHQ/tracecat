import { parseDocument } from "yaml"
import type { MCPIntegrationRead, RegistryActionReadMinimal } from "@/client"
import {
  buildSkillToolOptions,
  MAX_SKILL_TOOLS,
  readSkillFrontmatterTools,
  updateSkillFrontmatterTools,
} from "@/lib/skill-tools"

const registryAction: RegistryActionReadMinimal = {
  id: "action-1",
  name: "get_case",
  description: "Get one case.",
  namespace: "core.cases",
  type: "udf",
  origin: "tracecat_registry.core.cases",
  default_title: "Get case",
  display_group: "Cases",
  action: "core.cases.get_case",
}

const mcpIntegration: MCPIntegrationRead = {
  id: "integration-1",
  workspace_id: "workspace-1",
  name: "Slack",
  description: "Send and read Slack messages.",
  slug: "slack",
  server_type: "http",
  server_uri: "https://example.invalid/mcp",
  auth_type: "OAUTH2",
  oauth_integration_id: null,
  state: "connected",
  stdio_command: null,
  stdio_args: null,
  timeout: 30,
  tools: [
    {
      name: "post_message",
      description: "Post a message.",
      enabled: true,
      status: "available",
    },
    {
      name: "disabled_tool",
      enabled: false,
      status: "available",
    },
    {
      name: "removed_tool",
      enabled: true,
      status: "missing",
    },
  ],
  created_at: "2026-08-26T00:00:00.000Z",
  updated_at: "2026-08-26T00:00:00.000Z",
}

describe("skill frontmatter tools", () => {
  it("reads deduplicated tool IDs from metadata", () => {
    const state = readSkillFrontmatterTools(`name: incident-triage
metadata:
  tools:
    - core.cases.get_case
    - mcp.slack.post_message
    - core.cases.get_case`)

    expect(state).toEqual({
      valid: true,
      tools: ["core.cases.get_case", "mcp.slack.post_message"],
    })
  })

  it("updates only metadata.tools and preserves unrelated comments and keys", () => {
    const frontmatter = `name: incident-triage
# Keep this comment.
metadata:
  owner: security
  tools:
    - core.cases.get_case
license: MIT`

    const updated = updateSkillFrontmatterTools(frontmatter, [
      "mcp.slack.post_message",
    ])

    expect(updated).toContain("# Keep this comment.")
    expect(updated).toContain("owner: security")
    expect(updated).toContain("license: MIT")
    expect(updated).not.toContain("core.cases.get_case")
    expect(readSkillFrontmatterTools(updated)).toEqual({
      valid: true,
      tools: ["mcp.slack.post_message"],
    })
  })

  it("creates metadata.tools when metadata is absent", () => {
    const updated = updateSkillFrontmatterTools(
      "name: incident-triage\r\ndescription: Triage incidents.",
      ["core.cases.get_case"]
    )

    expect(updated).toContain(
      'metadata: { tools: ["core.cases.get_case"] }\r\n'
    )
    expect(readSkillFrontmatterTools(updated)).toEqual({
      valid: true,
      tools: ["core.cases.get_case"],
    })
  })

  it("reports malformed tools without rewriting the YAML", () => {
    expect(
      readSkillFrontmatterTools(`name: incident-triage
metadata:
  tools: core.cases.get_case`)
    ).toEqual({
      valid: false,
      message: "metadata.tools must be a YAML list.",
      tools: [],
    })
  })

  it("enforces the backend tool limit", () => {
    const tools = Array.from(
      { length: MAX_SKILL_TOOLS + 1 },
      (_, index) => `core.test.tool_${index}`
    )

    expect(() =>
      updateSkillFrontmatterTools("name: incident-triage", tools)
    ).toThrow(`Skills support at most ${MAX_SKILL_TOOLS} tools.`)
  })
})

describe("skill tool options", () => {
  it("combines registry actions with available MCP integration tools", () => {
    const options = buildSkillToolOptions(
      [
        registryAction,
        {
          ...registryAction,
          id: "action-2",
          name: "run_python",
          action: "core.script.run_python",
        },
      ],
      [mcpIntegration]
    )

    expect(options.map((option) => option.value)).toEqual([
      "core.cases.get_case",
      "mcp.slack",
      "mcp.slack.post_message",
    ])
    expect(
      options.find((option) => option.value === "mcp.slack")
    ).toMatchObject({
      label: "All tools",
      group: "Slack",
      kind: "mcp-integration",
    })
    expect(
      options.find((option) => option.value === "mcp.slack.post_message")
    ).toMatchObject({
      label: "post_message",
      description: "Post a message.",
      kind: "mcp-tool",
    })
  })
})

it("omits MCP options with unsupported names or noncanonical IDs", () => {
  const options = buildSkillToolOptions(
    [],
    [
      {
        ...mcpIntegration,
        tools: [
          "",
          "issue.get",
          "with space",
          "x".repeat(65),
          "x".repeat(256),
          "x\n",
          "issue_get",
          "x",
          "x".repeat(64),
        ].map((name) => ({ name })),
      },
    ]
  )
  expect(options.map((option) => option.value)).toEqual([
    "mcp.slack",
    "mcp.slack.issue_get",
    "mcp.slack.x",
    `mcp.slack.${"x".repeat(64)}`,
  ])
})

it.each([
  "metadata:\n  tools: &allowed [core.old]\ncopy: *allowed",
  "metadata: &metadata {tools: [core.old]}\ncopy: *metadata",
  "metadata: &metadata {owner: team}\ncopy: *metadata",
  "metadata: {tools: [&tool core.old]}\ncopy: *tool",
])("requires raw editing for aliased values: %s", (source) => {
  expect(readSkillFrontmatterTools(source).valid).toBe(false)
  expect(() => updateSkillFrontmatterTools(source, ["core.new"])).toThrow(
    "Edit tools in the YAML editor"
  )
})

it("offers only whole-server grants for stdio integrations", () => {
  expect(
    buildSkillToolOptions(
      [],
      [{ ...mcpIntegration, server_type: "stdio" }]
    ).map((option) => option.value)
  ).toEqual(["mcp.slack"])
})

it.each([
  "defaults: &defaults\n  metadata: { tools: [core.old], revision: 1 }\n<<: *defaults",
  "defaults: &defaults { tools: [core.old], revision: 1 }\nmetadata:\n  <<: *defaults",
])("blocks structured edits of merged metadata", (source) => {
  expect(readSkillFrontmatterTools(source)).toMatchObject({ valid: false })
  expect(() => updateSkillFrontmatterTools(source, ["core.new"])).toThrow(
    "merge keys"
  )
})

it.each(["", "   "])(
  "rejects blank tool IDs without hiding the error",
  (value) => {
    const source = `metadata: { tools: [${JSON.stringify(value)}] }`
    expect(readSkillFrontmatterTools(source)).toMatchObject({
      valid: false,
      message: "metadata.tools must not contain blank tool IDs.",
    })
    expect(() => updateSkillFrontmatterTools(source, ["core.new"])).toThrow(
      "blank"
    )
  }
)

it("checks the raw declaration count before deduplicating", () => {
  const source = `metadata: { tools: ${JSON.stringify(Array(65).fill("core.example"))} }`
  expect(readSkillFrontmatterTools(source)).toMatchObject({
    valid: false,
    message: "metadata.tools supports at most 64 tool IDs.",
  })
})

it("disambiguates duplicate integration names in options and chips", () => {
  const options = buildSkillToolOptions(
    [],
    [
      mcpIntegration,
      {
        ...mcpIntegration,
        id: "integration-2",
        slug: "slack-2",
      },
    ]
  )
  expect(options.find((option) => option.value === "mcp.slack")).toMatchObject({
    group: "Slack (slack)",
    tagGroup: "Slack (slack)",
  })
  expect(
    options.find((option) => option.value === "mcp.slack-2.post_message")
  ).toMatchObject({
    group: "Slack (slack-2)",
    tagGroup: "Slack (slack-2)",
  })
})

it.each([
  "name: example\nmetadata: { revision: 0123, tools: [core.old] }",
  "name: example\nmetadata: { revision: 0123 }",
  "name: example\nrevision: 0123",
  "{ name: example, revision: 0123 }",
  "name: example\nmetadata:\n  revision: 0123\n  tools:\n    - core.old\nother: yes",
  "name: example\nmetadata:\n  revision: 0123\nother: yes",
  "name: example\nmetadata: {}",
])("preserves untouched YAML scalar source: %s", (source) => {
  const updated = updateSkillFrontmatterTools(source, ["core.new"])
  if (source.includes("revision: 0123")) {
    expect(updated).toContain("revision: 0123")
  }
  expect(readSkillFrontmatterTools(updated)).toEqual({
    valid: true,
    tools: ["core.new"],
  })
  if (source.includes("other: yes")) expect(updated).toContain("other: yes")
})

it("preserves CRLF and block sequence anchors", () => {
  const source =
    "name: example\r\nmetadata:\r\n  tools: &allowed\r\n    - core.old\r\n"
  const updated = updateSkillFrontmatterTools(source, ["core.new"])
  expect(updated.replace(/\r\n/g, "")).not.toContain("\n")
  expect(updated).toContain("&allowed")
  expect(parseDocument(updated).toJS().metadata.tools).toEqual(["core.new"])
})

it.each([
  "not-a-tool",
  "mcp.server.issue.get",
  "core.Upper",
  "core." + "x".repeat(256),
])("keeps malformed IDs visible and removable: %s", (id) => {
  const source = `metadata: { tools: ${JSON.stringify([id, "core.ok"])} }`
  expect(readSkillFrontmatterTools(source)).toMatchObject({
    valid: false,
    canRemove: true,
    tools: [id, "core.ok"],
  })
  expect(
    readSkillFrontmatterTools(updateSkillFrontmatterTools(source, ["core.ok"]))
  ).toEqual({ valid: true, tools: ["core.ok"] })
  expect(() =>
    updateSkillFrontmatterTools(source, [id, "core.ok", "core.new"])
  ).toThrow("Invalid tool IDs")
})

it("allows removing malformed IDs one at a time", () => {
  const source = 'metadata: { tools: ["bad-one", "bad-two", "core.ok"] }'
  const updated = updateSkillFrontmatterTools(source, ["bad-two", "core.ok"])
  expect(readSkillFrontmatterTools(updated)).toMatchObject({
    valid: false,
    canRemove: true,
    tools: ["bad-two", "core.ok"],
  })
})

it.each([
  "core.cases.removed",
  "mcp.deleted",
  "mcp.deleted.read",
  "mcp.slack.unknown",
  "mcp.slack.disabled_tool",
  "mcp.slack.removed_tool",
])("preserves unavailable ID %s for removal", (id) => {
  const source = `metadata: {tools: ["${id}", "core.cases.get_case"]}`
  expect(
    readSkillFrontmatterTools(source, [mcpIntegration], [registryAction])
  ).toMatchObject({
    valid: false,
    canRemove: true,
    tools: [id, "core.cases.get_case"],
    message: expect.stringContaining(`Unavailable tool IDs: ${id}`),
  })
})

it("accepts available registry, whole-server, and individual MCP grants", () => {
  expect(
    readSkillFrontmatterTools(
      "metadata: {tools: [core.cases.get_case, mcp.slack, mcp.slack.post_message]}",
      [mcpIntegration],
      [registryAction]
    ).valid
  ).toBe(true)
})

it("distinguishes unknown catalogs from loaded empty catalogs", () => {
  const source = "metadata: {tools: [core.cases.get_case, mcp.slack]}"
  expect(readSkillFrontmatterTools(source).valid).toBe(true)
  expect(readSkillFrontmatterTools(source, [], []).valid).toBe(false)
})
