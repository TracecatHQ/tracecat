import type { AgentPresetRead } from "@/client"
import {
  AGENT_PRESET_PUBLISHING_FIELDS,
  type AgentPresetReasoningFields,
  buildAgentPresetUpdatePayload,
  buildDuplicateAgentPresetPayload,
  buildDuplicateAgentSlug,
  buildSkillCommandItemValue,
  canSubmitAgentPresetForm,
  getAgentPresetErrorMessage,
  readAgentPresetReasoningEffort,
} from "@/lib/agent-presets"

describe("getAgentPresetErrorMessage", () => {
  it("explains the effective tool count and limit", () => {
    const error = Object.assign(new Error("Bad request"), {
      body: {
        detail: {
          code: "agent_tool_limit_exceeded",
          message: "Invalid preset configuration",
          tool_count: 140,
          max_tools: 128,
        },
      },
    })

    expect(getAgentPresetErrorMessage(error, "Save failed.")).toBe(
      "This agent has 140 tools; the limit is 128."
    )
  })

  it("shows the message from a structured validation detail", () => {
    const error = Object.assign(new Error("Bad request"), {
      body: {
        detail: {
          code: "skill_not_published",
          message: "Only published skills can be attached.",
        },
      },
    })
    expect(getAgentPresetErrorMessage(error, "Save failed.")).toBe(
      "Only published skills can be attached."
    )
  })

  it("preserves a string API detail", () => {
    const error = Object.assign(new Error("Bad request"), {
      body: { detail: "Agent preset slug already exists" },
    })

    expect(getAgentPresetErrorMessage(error, "Save failed.")).toBe(
      "Agent preset slug already exists"
    )
  })

  it.each([null, undefined, {}, "unknown error"])(
    "uses the fallback when no API message is available: %p",
    (error) => {
      expect(getAgentPresetErrorMessage(error, "Save failed.")).toBe(
        "Save failed."
      )
    }
  )
})

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
  "library_skills",
  "agents",
  "retries",
  "reasoning_effort",
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
      reasoningEffortChanged: false,
    })

    expect(update).not.toHaveProperty("skills")
  })

  it("includes changed skill bindings in preset updates", () => {
    const update = buildAgentPresetUpdatePayload(presetPayload, {
      skillsChanged: true,
      reasoningEffortChanged: false,
    })

    expect(update.skills).toEqual(presetPayload.skills)
  })

  it("sends the reasoning level only when the control changed", () => {
    const payload = { ...presetPayload, reasoning_effort: null }

    expect(
      buildAgentPresetUpdatePayload(payload, {
        skillsChanged: false,
        reasoningEffortChanged: false,
      })
    ).not.toHaveProperty("reasoning_effort")
    expect(
      buildAgentPresetUpdatePayload(payload, {
        skillsChanged: false,
        reasoningEffortChanged: true,
      }).reasoning_effort
    ).toBeNull()
  })
})

describe("readAgentPresetReasoningEffort", () => {
  it.each<[AgentPresetReasoningFields, string | null]>([
    [{ reasoning_effort: "high" }, "high"],
    [{ reasoning_effort: null }, null],
    // An explicit level, even null, wins over the legacy flag.
    [{ reasoning_effort: null, enable_thinking: false }, null],
    // Responses from API pods that predate reasoning levels.
    [{ enable_thinking: false }, "off"],
    [{ enable_thinking: true }, null],
    [{}, null],
  ])("reads %p as %p", (fields, expected) => {
    expect(readAgentPresetReasoningEffort(fields)).toBe(expected)
  })
})

describe("buildDuplicateAgentPresetPayload with a legacy preset", () => {
  it("duplicates an off preset read from an older API pod as off", () => {
    // API pods from before reasoning levels return only the legacy flag.
    const legacyOffPreset: AgentPresetRead & AgentPresetReasoningFields = {
      id: "preset-1",
      workspace_id: "ws-1",
      name: "Triage agent",
      slug: "triage-agent",
      model_name: "gpt-4o-mini",
      model_provider: "openai",
      enable_thinking: false,
      created_at: "2026-03-13T12:00:00Z",
      updated_at: "2026-03-13T12:00:00Z",
    }

    expect(
      buildDuplicateAgentPresetPayload(legacyOffPreset, []).reasoning_effort
    ).toBe("off")
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
        library_skills: ["phishing-triage"],
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
    expect(duplicated.library_skills).toEqual(["phishing-triage"])
    expect(duplicated.enable_internet_access).toBe(true)
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
