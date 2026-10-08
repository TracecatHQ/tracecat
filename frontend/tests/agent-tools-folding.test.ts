import {
  getActionFieldLabel,
  getAgentToolsFolding,
  getVisibleOptionalFields,
  removeOptionalField,
  withoutFoldedFields,
} from "@/lib/agent-tools-folding"

const agentProperties: Record<string, unknown> = {
  actions: {
    type: ["array", "null"],
    "x-tracecat-component": [{ component_id: "action-type", multiple: true }],
  },
  mcp_integrations: {
    type: ["array", "null"],
    "x-tracecat-component": [{ component_id: "mcp-integration" }],
  },
  tool_approvals: { type: ["object", "null"] },
  instructions: { type: ["string", "null"] },
}
const httpProperties: Record<string, unknown> = {
  url: { type: "string" },
  headers: { type: ["object", "null"] },
  tool_approvals: { type: ["object", "null"] },
}
const optionalFields = (properties: Record<string, unknown>) =>
  Object.entries(properties)

function visible(
  properties: Record<string, unknown>,
  inputs: Record<string, unknown>,
  manual: { shown?: string[]; hidden?: string[] } = {}
) {
  return getVisibleOptionalFields({
    optionalFields: optionalFields(properties),
    inputs,
    folding: getAgentToolsFolding(properties, inputs),
    manuallyVisible: new Set(manual.shown),
    manuallyHidden: new Set(manual.hidden),
    showByDefault: () => false,
  })
}

describe("getAgentToolsFolding", () => {
  it("folds MCP and approvals into a multi-select action field", () => {
    expect(getAgentToolsFolding(agentProperties, {})).toEqual({
      actionsField: "actions",
      mcpField: "mcp_integrations",
      approvalsField: "tool_approvals",
      foldedFields: new Set(["mcp_integrations", "tool_approvals"]),
    })
  })

  it("requires multiple on action-type but not on mcp-integration", () => {
    const single = {
      ...agentProperties,
      actions: {
        type: "string",
        "x-tracecat-component": [{ component_id: "action-type" }],
      },
    }
    expect(getAgentToolsFolding(single, {})).toEqual({
      actionsField: null,
      mcpField: null,
      approvalsField: null,
      foldedFields: new Set(),
    })
    const explicitSingle = {
      ...agentProperties,
      actions: {
        type: "string",
        "x-tracecat-component": [
          { component_id: "action-type", multiple: false },
        ],
      },
    }
    expect(getAgentToolsFolding(explicitSingle, {}).actionsField).toBeNull()
  })

  it("treats null like unset for every owned arg", () => {
    const folding = getAgentToolsFolding(agentProperties, {
      actions: null,
      mcp_integrations: null,
      tool_approvals: null,
    })
    expect(folding.mcpField).toBe("mcp_integrations")
    expect(folding.approvalsField).toBe("tool_approvals")
  })

  it("leaves values the UI cannot represent to their own fields", () => {
    expect(
      getAgentToolsFolding(agentProperties, {
        mcp_integrations: "${{ ACTIONS.servers.result }}",
        tool_approvals: { "tools.test.first": "sometimes" },
      }).foldedFields
    ).toEqual(new Set())
    expect(
      getAgentToolsFolding(agentProperties, { actions: "${{ ACTIONS.x }}" })
        .foldedFields
    ).toEqual(new Set())
  })
})

describe("optional field visibility", () => {
  it("excludes folded args from the field list and the dropdown", () => {
    const folding = getAgentToolsFolding(agentProperties, {})
    expect(
      withoutFoldedFields(optionalFields(agentProperties), folding).map(
        ([fieldName]) => fieldName
      )
    ).toEqual(["actions", "instructions"])
    expect(getActionFieldLabel("actions", folding)).toBe("Tools")
    expect(getActionFieldLabel("max_tool_calls", folding)).toBe(
      "Max tool calls"
    )
  })

  it("shows Tools when only a folded sibling has a value", () => {
    expect(
      visible(agentProperties, { tool_approvals: { "tools.test.first": true } })
    ).toEqual(new Set(["actions"]))
    expect(
      visible(agentProperties, { mcp_integrations: ["mcp-test"] })
    ).toEqual(new Set(["actions"]))
    expect(visible(agentProperties, {})).toEqual(new Set())
  })

  it("keeps an unfolded sibling as a field of its own", () => {
    const inputs = { mcp_integrations: "${{ ACTIONS.servers.result }}" }
    const folding = getAgentToolsFolding(agentProperties, inputs)
    expect(visible(agentProperties, inputs)).toEqual(
      new Set(["mcp_integrations"])
    )
    expect(
      withoutFoldedFields(optionalFields(agentProperties), folding).map(
        ([fieldName]) => fieldName
      )
    ).toEqual(["actions", "mcp_integrations", "instructions"])
  })

  it("applies manual choices and default-visible fields", () => {
    expect(
      visible(agentProperties, { actions: [] }, { hidden: ["actions"] })
    ).toEqual(new Set())
    expect(visible(agentProperties, {}, { shown: ["instructions"] })).toEqual(
      new Set(["instructions"])
    )
    expect(
      getVisibleOptionalFields({
        optionalFields: optionalFields(agentProperties),
        inputs: {},
        folding: getAgentToolsFolding(agentProperties, {}),
        manuallyVisible: new Set(),
        manuallyHidden: new Set(),
        showByDefault: (fieldName) => fieldName === "instructions",
      })
    ).toEqual(new Set(["instructions"]))
  })

  it("clears folded siblings when Tools is hidden, and only then", () => {
    const inputs = {
      prompt: "Investigate",
      actions: ["tools.test.first"],
      mcp_integrations: ["mcp-test"],
      tool_approvals: { "tools.test.first": true },
      instructions: "Be brief",
    }
    const folding = getAgentToolsFolding(agentProperties, inputs)
    expect(removeOptionalField(inputs, "actions", folding)).toEqual({
      prompt: "Investigate",
      instructions: "Be brief",
    })
    expect(removeOptionalField(inputs, "instructions", folding)).toEqual({
      prompt: "Investigate",
      actions: ["tools.test.first"],
      mcp_integrations: ["mcp-test"],
      tool_approvals: { "tools.test.first": true },
    })
  })

  it("leaves non-agent actions unaffected", () => {
    const inputs = { url: "https://example.com", tool_approvals: { a: true } }
    const folding = getAgentToolsFolding(httpProperties, inputs)
    expect(folding.foldedFields).toEqual(new Set())
    expect(
      withoutFoldedFields(optionalFields(httpProperties), folding)
    ).toEqual(optionalFields(httpProperties))
    expect(visible(httpProperties, inputs)).toEqual(
      new Set(["url", "tool_approvals"])
    )
    expect(getActionFieldLabel("tool_approvals", folding)).toBe(
      "Tool approvals"
    )
    expect(removeOptionalField(inputs, "tool_approvals", folding)).toEqual({
      url: "https://example.com",
    })
  })
})
