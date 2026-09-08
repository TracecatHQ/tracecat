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

    expect(updated).toContain("\r\nmetadata:\r\n  tools:\r\n")
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

it("omits MCP options that cannot form canonical tool IDs", () => {
  const options = buildSkillToolOptions(
    [],
    [
      {
        ...mcpIntegration,
        tools: ["issue.get", "with space", "x".repeat(256), "issue_get"].map(
          (name) => ({ name })
        ),
      },
    ]
  )
  expect(options.map((option) => option.value)).toEqual([
    "mcp.slack",
    "mcp.slack.issue_get",
  ])
})

it("preserves anchors referenced outside the tools list", () => {
  const updated = updateSkillFrontmatterTools(
    "name: example\nmetadata:\n  tools: &allowed [core.old]\ncopy: *allowed",
    ["core.new"]
  )
  expect(parseDocument(updated).toJS()).toEqual({
    name: "example",
    metadata: { tools: ["core.new"] },
    copy: ["core.new"],
  })
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
