import { canSubmitAgentPresetForm } from "@/lib/agent-presets"

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
})
