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

  it("still allows save for edited presets when the form is dirty", () => {
    expect(
      canSubmitAgentPresetForm({
        mode: "edit",
        isDirty: true,
        name: "Existing agent",
        modelProvider: "",
        modelName: "",
      })
    ).toBe(true)
  })
})
