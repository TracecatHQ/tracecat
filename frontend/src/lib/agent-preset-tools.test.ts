import {
  applyToolSelection,
  buildToolIndex,
  getBlockedActions,
  removeTools,
  searchTools,
  setToolApproval,
} from "@/lib/agent-preset-tools"
import {
  mcpIntegration,
  registryTool,
} from "../../tests/fixtures/agent-preset-tools"

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
  expect(removeTools(fields, new Set(["b"]))).toEqual({
    actions: ["unknown", "a"],
    toolApprovals: fields.toolApprovals.slice(1),
  })
  const same = removeTools(fields, new Set(["absent"]))
  expect(same.actions).toBe(fields.actions)
  expect(same.toolApprovals).toBe(fields.toolApprovals)
})

it("updates approvals in place and reuses arrays for no-ops", () => {
  const rules = [
    { tool: "first", allow: false },
    { tool: "second", allow: true },
  ]
  expect(setToolApproval(rules, "second", true)).toBe(rules)
  expect(setToolApproval(rules, "first", false)).toBe(rules)
  expect(setToolApproval(rules, "missing", false)).toBe(rules)
  expect(setToolApproval(rules, "first", true)).toEqual([
    { tool: "first", allow: true },
    rules[1],
  ])
  expect(setToolApproval(rules, "second", false)).toEqual([rules[0]])
  expect(setToolApproval(rules, "third", true)).toEqual([
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
