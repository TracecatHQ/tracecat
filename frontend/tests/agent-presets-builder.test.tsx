import {
  AGENT_PRESET_PUBLISHING_FIELDS,
  buildAgentPresetUpdatePayload,
  buildAuthoredAgentsConfig,
  buildDuplicateAgentPresetPayload,
  buildDuplicateAgentSlug,
  buildSkillCommandItemValue,
  canSubmitAgentPresetForm,
} from "@/lib/agent-presets"

const presetPayload = {
  name: "Triage agent",
  model_name: "gpt-4o-mini",
  model_provider: "openai",
  skills: [{ skill_id: "784dd826-072e-46f1-95a4-08d3417c784f" }],
}

// Mirrors `AgentPresetService.EXECUTION_FIELDS` in
// `tracecat/agent/preset/service.py`. Update both sides together.
const BACKEND_EXECUTION_FIELDS = [
  "instructions",
  "model_name",
  "model_provider",
  "catalog_id",
  "base_url",
  "output_type",
  "actions",
  "namespaces",
  "tool_approvals",
  "mcp_integrations",
  "agents",
  "retries",
  "enable_thinking",
  "enable_internet_access",
]

describe("AGENT_PRESET_PUBLISHING_FIELDS", () => {
  it("matches the backend execution fields that cut a new preset version", () => {
    expect([...AGENT_PRESET_PUBLISHING_FIELDS].sort()).toEqual(
      [...BACKEND_EXECUTION_FIELDS].sort()
    )
  })
})

describe("buildAgentPresetUpdatePayload", () => {
  it("omits unchanged skill bindings from preset updates", () => {
    const update = buildAgentPresetUpdatePayload(presetPayload, {
      skillsChanged: false,
    })

    expect(update).not.toHaveProperty("skills")
  })

  it("includes changed skill bindings in preset updates", () => {
    const update = buildAgentPresetUpdatePayload(presetPayload, {
      skillsChanged: true,
    })

    expect(update.skills).toEqual(presetPayload.skills)
  })
})

describe("buildAuthoredAgentsConfig", () => {
  it("keeps resolved preset IDs out of authored API payloads", () => {
    const agents = buildAuthoredAgentsConfig({
      enabled: true,
      subagents: [
        {
          preset: " analyst ",
          presetId: "11111111-1111-1111-1111-111111111111",
          name: " investigator ",
          description: " Investigates alerts ",
          maxTurns: "5",
        },
      ],
    })

    expect(agents).toEqual({
      enabled: true,
      subagents: [
        {
          preset: "analyst",
          name: "investigator",
          description: "Investigates alerts",
          max_turns: 5,
        },
      ],
    })
    expect(agents.subagents?.[0]).not.toHaveProperty("preset_id")
  })
})

describe("canSubmitAgentPresetForm", () => {
  it("keeps save disabled for new presets until model config is present", () => {
    expect(
      canSubmitAgentPresetForm({
        mode: "create",
        isDirty: true,
        name: "QA Save Debug Agent",
        modelProvider: "",
        modelName: "",
      })
    ).toBe(false)
  })

  it("allows save for new presets once required model config is present", () => {
    expect(
      canSubmitAgentPresetForm({
        mode: "create",
        isDirty: false,
        name: "QA Save Debug Agent",
        modelProvider: "openai",
        modelName: "gpt-4o-mini",
      })
    ).toBe(true)
  })

  it("allows save for edited presets when the form is dirty and required fields are present", () => {
    expect(
      canSubmitAgentPresetForm({
        mode: "edit",
        isDirty: true,
        name: "Existing agent",
        modelProvider: "openai",
        modelName: "gpt-4o-mini",
      })
    ).toBe(true)
  })

  it("keeps save disabled for edited presets when required fields are whitespace only", () => {
    expect(
      canSubmitAgentPresetForm({
        mode: "edit",
        isDirty: true,
        name: "   ",
        modelProvider: "   ",
        modelName: "   ",
      })
    ).toBe(false)
  })

  it("builds stable duplicate agent slugs with numeric suffixes", () => {
    expect(buildDuplicateAgentSlug("triage-agent", [])).toBe(
      "copy-of-triage-agent"
    )
    expect(
      buildDuplicateAgentSlug("triage-agent", ["copy-of-triage-agent"])
    ).toBe("copy-of-triage-agent-2")
    expect(
      buildDuplicateAgentSlug("triage-agent", [
        "copy-of-triage-agent",
        "copy-of-triage-agent-2",
      ])
    ).toBe("copy-of-triage-agent-3")
  })

  it("copies agent preset payload fields while renaming the duplicate", () => {
    const duplicated = buildDuplicateAgentPresetPayload(
      {
        id: "preset-1",
        workspace_id: "ws-1",
        name: "Triage agent",
        slug: "triage-agent",
        description: "Handles inbound incidents",
        instructions: "Investigate alerts",
        model_name: "gpt-4o-mini",
        model_provider: "openai",
        base_url: null,
        output_type: null,
        actions: ["core.http_request"],
        namespaces: ["core.http_request"],
        tool_approvals: { "core.http_request": true },
        mcp_integrations: ["mcp-1"],
        agents: {
          enabled: true,
          subagents: [
            {
              preset: "evidence-agent",
              preset_id: "11111111-1111-1111-1111-111111111111",
              preset_version_id: "22222222-2222-2222-2222-222222222222",
              preset_version: 3,
              name: "investigator",
              description: "Investigates alerts",
              max_turns: 5,
            },
          ],
        },
        retries: 2,
        enable_internet_access: true,
        created_at: "2026-03-13T12:00:00Z",
        updated_at: "2026-03-13T12:00:00Z",
      },
      ["triage-agent"]
    )

    expect(duplicated.name).toBe("Copy of Triage agent")
    expect(duplicated.slug).toBe("copy-of-triage-agent")
    expect(duplicated.instructions).toBe("Investigate alerts")
    expect(duplicated.actions).toEqual(["core.http_request"])
    expect(duplicated.enable_internet_access).toBe(true)
    expect(duplicated.agents).toEqual({
      enabled: true,
      subagents: [
        {
          preset: "evidence-agent",
          name: "investigator",
          description: "Investigates alerts",
          max_turns: 5,
        },
      ],
    })
    expect(duplicated.agents?.subagents?.[0]).not.toHaveProperty("preset_id")
    expect(duplicated.agents?.subagents?.[0]).not.toHaveProperty(
      "preset_version_id"
    )
  })

  it("keeps skill picker command values safe when skill descriptions contain selector metacharacters", () => {
    const skillId = "784dd826-072e-46f1-95a4-08d3417c784f"
    const skillName = "investigate-mailbox-delegation"
    const unsafeDescription =
      '"][data-value="investigate-mailbox-delegation use this skill to investigate alerts"]'
    const value = buildSkillCommandItemValue({
      id: skillId,
      name: skillName,
      description: unsafeDescription,
    })

    expect(value).not.toContain(unsafeDescription)
    expect(value).toContain(skillName)
    expect(value).toContain("use-this-skill-to-investigate-alerts")
    expect(() => {
      document.querySelector(`[cmdk-item=""][data-value="${value}"]`)
    }).not.toThrow()
  })

  it("keeps skill descriptions searchable in safe command values", () => {
    const value = buildSkillCommandItemValue({
      id: "784dd826-072e-46f1-95a4-08d3417c784f",
      name: "mailbox-skill",
      description: "Investigates delegation alerts",
    })

    expect(value).toContain("investigates-delegation-alerts")
  })

  it("keeps non-ASCII skill descriptions searchable in safe command values", () => {
    const value = buildSkillCommandItemValue({
      id: "784dd826-072e-46f1-95a4-08d3417c784f",
      name: "mailbox-skill",
      description: "メール調査 Café alerts",
    })

    expect(value).toContain("メール調査-café-alerts")
    expect(() => {
      document.querySelector(`[cmdk-item=""][data-value="${value}"]`)
    }).not.toThrow()
  })
})
