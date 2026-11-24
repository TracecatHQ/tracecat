import {
  applyToolSelection,
  buildToolIndex,
  getBlockedActions,
  getToolApproval,
  isInNamespaces,
  removeTools,
  searchTools,
  setToolApproval,
  withChannelTools,
} from "@/lib/agent-preset-tools"
import {
  mcpIntegration,
  registryTool,
} from "../../tests/fixtures/agent-preset-tools"

const NO_TOOLS = buildToolIndex([], [], { filterAgentTools: false })

it("keeps stored order, unknown keys, and appends additions in registry order", () => {
  const current = ["b", "unknown", "a"]
  expect(
    applyToolSelection(current, new Set(["d", "a", "unknown", "c"]), [
      "a",
      "b",
      "c",
      "d",
    ])
  ).toEqual(["unknown", "a", "c", "d"])
  expect(applyToolSelection(current, new Set(current), ["a", "b"])).toBe(
    current
  )
  expect(
    applyToolSelection(current, new Set([...current, "new"]), ["new"])
  ).toEqual([...current, "new"])
})

it("removes active approvals on deselect but preserves false and unrelated rules", () => {
  const fields = {
    actions: ["b", "unknown", "a"],
    toolApprovals: [
      { tool: "b", allow: true },
      { tool: "b", allow: false },
      { tool: "skill.test", allow: true },
    ],
  }
  expect(removeTools(fields, new Set(["b"]), NO_TOOLS)).toEqual({
    actions: ["unknown", "a"],
    toolApprovals: fields.toolApprovals.slice(1),
  })
  const same = removeTools(fields, new Set(["absent"]), NO_TOOLS)
  expect(same.actions).toBe(fields.actions)
  expect(same.toolApprovals).toBe(fields.toolApprovals)
})

it("updates approvals in place and reuses arrays for no-ops", () => {
  const rules = [
    { tool: "first", allow: false },
    { tool: "second", allow: true },
  ]
  expect(setToolApproval(rules, "second", true, NO_TOOLS)).toBe(rules)
  expect(setToolApproval(rules, "first", false, NO_TOOLS)).toBe(rules)
  expect(setToolApproval(rules, "missing", false, NO_TOOLS)).toBe(rules)
  expect(setToolApproval(rules, "first", true, NO_TOOLS)).toEqual([
    { tool: "first", allow: true },
    rules[1],
  ])
  expect(setToolApproval(rules, "second", false, NO_TOOLS)).toEqual([rules[0]])
  expect(setToolApproval(rules, "third", true, NO_TOOLS)).toEqual([
    ...rules,
    { tool: "third", allow: true },
  ])
})

it("uses startsWith filters without expanding grants", () => {
  const actions = [
    "core.cases.get",
    "core.cases_extra.get",
    "tools.example.list",
  ]
  expect(getBlockedActions(actions, ["core.cases"])).toEqual([
    "tools.example.list",
  ])
  expect(getBlockedActions(actions, ["core.cases.", "tools."])).toEqual([
    "core.cases_extra.get",
  ])
  expect(getBlockedActions(actions, [])).toEqual([])
  expect(getBlockedActions(actions, [""])).toEqual([])
})

it("groups MCP, Tracecat and custom tools, excluding unselectable actions", () => {
  const index = buildToolIndex(
    [
      registryTool("tools.custom.one", {
        origin: "git+ssh://git@example.com/registry",
        display_group: "Custom",
      }),
      registryTool("core.script.run_python"),
      registryTool("tools.test.one"),
      registryTool("tools.test.two", { display_group: "Test tools" }),
      registryTool("tools.test.three", {
        display_group: "Ignored later title",
      }),
    ],
    [mcpIntegration()]
  )
  expect(index.groups.map((group) => group.section)).toEqual([
    "mcp",
    "tracecat",
    "custom",
  ])
  expect(index.groups[1].title).toBe("Test tools")
  expect(index.byKey.has("core.script.run_python")).toBe(false)
  expect(
    buildToolIndex([registryTool("core.script.run_python")], [], {
      filterAgentTools: false,
    }).byKey.has("core.script.run_python")
  ).toBe(true)
  expect(index.entries[0].key).toBe("tools.custom.one")
})

it("matches prepared titles and keys only, and caps results at 200", () => {
  const index = buildToolIndex([
    registryTool("core.test.key_token", {
      default_title: "Unique title",
      description: "descriptiononly",
    }),
    ...Array.from({ length: 1500 }, (_, i) =>
      registryTool(`tools.sample.action_${i}`)
    ),
  ])
  expect(searchTools(index, "Unique title")[0].key).toBe("core.test.key_token")
  expect(searchTools(index, "key_token")[0].key).toBe("core.test.key_token")
  expect(searchTools(index, "descriptiononly")).toEqual([])
  expect(searchTools(index, "action")).toHaveLength(200)
})

it("resolves explicit approvals before registry defaults, including unknown keys", () => {
  const index = buildToolIndex(
    [
      registryTool("tools.test.ask", { requires_approval: true }),
      registryTool("tools.test.auto"),
    ],
    [mcpIntegration()]
  )
  expect(index.byKey.get("tools.test.ask")?.defaultAsk).toBe(true)
  expect(index.byKey.get("mcp:mcp-test")?.defaultAsk).toBe(false)
  expect(getToolApproval([], "tools.test.ask", index)).toBe(true)
  expect(getToolApproval([], "tools.test.auto", index)).toBe(false)
  expect(getToolApproval([], "unknown", index)).toBe(false)
  expect(
    getToolApproval(
      [{ tool: "tools.test.ask", allow: false }],
      "tools.test.ask",
      index
    )
  ).toBe(false)
  expect(
    getToolApproval([{ tool: "unknown", allow: true }], "unknown", index)
  ).toBe(true)
})

it("sets default-ask overrides in order and reuses unchanged approval arrays", () => {
  const index = buildToolIndex(
    ["first", "second", "missing", "third"].map((key) =>
      registryTool(key, { requires_approval: true })
    ),
    [],
    { filterAgentTools: false }
  )
  const rules = [
    { tool: "first", allow: true },
    { tool: "second", allow: false },
  ]
  expect(setToolApproval(rules, "first", true, index)).toBe(rules)
  expect(setToolApproval(rules, "second", false, index)).toBe(rules)
  expect(setToolApproval(rules, "missing", true, index)).toBe(rules)
  expect(setToolApproval(rules, "first", false, index)).toEqual([
    { tool: "first", allow: false },
    rules[1],
  ])
  expect(setToolApproval(rules, "third", false, index)).toEqual([
    ...rules,
    { tool: "third", allow: false },
  ])
  expect(setToolApproval(rules, "second", true, index)).toEqual([rules[0]])
})

it("removes a removed tool's rules, retaining false rules on default-auto tools", () => {
  const fields = {
    actions: ["ask", "auto"],
    toolApprovals: [
      { tool: "ask", allow: false },
      { tool: "ask", allow: true },
      { tool: "auto", allow: false },
      { tool: "auto", allow: true },
      { tool: "other", allow: false },
    ],
  }
  const index = buildToolIndex(
    [registryTool("ask", { requires_approval: true }), registryTool("auto")],
    [],
    { filterAgentTools: false }
  )
  expect(removeTools(fields, new Set(fields.actions), index)).toEqual({
    actions: [],
    toolApprovals: [fields.toolApprovals[2], fields.toolApprovals[4]],
  })
  const unchanged = removeTools(fields, new Set(["missing"]), index)
  expect(unchanged.actions).toBe(fields.actions)
  expect(unchanged.toolApprovals).toBe(fields.toolApprovals)
})

it("keeps approval rules for removed tools still granted by another source", () => {
  const fields = {
    actions: ["retained", "removed"],
    toolApprovals: [
      { tool: "retained", allow: true },
      { tool: "retained", allow: false },
      { tool: "removed", allow: true },
      { tool: "removed", allow: false },
    ],
  }
  expect(
    removeTools(
      fields,
      new Set(fields.actions),
      buildToolIndex(
        fields.actions.map((key) =>
          registryTool(key, { requires_approval: true })
        ),
        [],
        { filterAgentTools: false }
      ),
      new Set(["retained"])
    )
  ).toEqual({
    actions: [],
    toolApprovals: fields.toolApprovals.slice(0, 2),
  })
})

describe("isInNamespaces", () => {
  it("matches whole namespace segments only", () => {
    expect(isInNamespaces("tools.slack.post_message", ["tools.slack"])).toBe(
      true
    )
    expect(isInNamespaces("tools.slack", ["tools.slack"])).toBe(true)
    expect(isInNamespaces("tools.slackbot.post_message", ["tools.slack"])).toBe(
      false
    )
    expect(isInNamespaces("core.http_request", [])).toBe(false)
  })
})

describe("withChannelTools", () => {
  const channelGroup = {
    provider: "slack",
    title: "Slack",
    label: "as @Tracecat",
    tools: [
      {
        name: "internal.slack.post_message",
        title: "Post message",
        description: "Post a message.",
        requires_approval: true,
      },
      {
        name: "internal.slack.list_channels",
        title: "List channels",
        description: "List channels.",
        requires_approval: false,
      },
    ],
  }

  it("resolves channel tool approvals from requires_approval and stored rules", () => {
    const base = buildToolIndex([registryTool("core.http_request")])
    const index = withChannelTools(base, [channelGroup])
    const post = "internal.slack.post_message"
    const list = "internal.slack.list_channels"

    expect(getToolApproval([], post, index)).toBe(true)
    expect(getToolApproval([], list, index)).toBe(false)
    expect(getToolApproval([{ tool: post, allow: false }], post, index)).toBe(
      false
    )
    // Search and groups never list channel tools.
    expect(index.entries).toBe(base.entries)
    expect(index.groups).toBe(base.groups)
    expect(searchTools(index, "post")).toEqual([])
    expect(withChannelTools(base, [])).toBe(base)
  })
})
