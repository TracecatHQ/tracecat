import {
  buildWorkspaceModelSettingsUpdate,
  workspaceModelSettingsSchema,
} from "@/components/settings/workspace-model-settings"

describe("workspace model settings", () => {
  it("clears the workspace subset when inheriting organization models", () => {
    expect(
      buildWorkspaceModelSettingsUpdate({
        inherit_agent_enabled_models: true,
        agent_enabled_model_refs: ["openai/gpt-4.1"],
      })
    ).toEqual({
      agent_enabled_model_refs: null,
    })
  })

  it("keeps the explicit workspace subset when inheritance is disabled", () => {
    expect(
      buildWorkspaceModelSettingsUpdate({
        inherit_agent_enabled_models: false,
        agent_enabled_model_refs: ["anthropic/claude-sonnet-4"],
      })
    ).toEqual({
      agent_enabled_model_refs: ["anthropic/claude-sonnet-4"],
    })
  })

  it("defaults an omitted model list to an empty subset", () => {
    const result = workspaceModelSettingsSchema.safeParse({
      inherit_agent_enabled_models: false,
    })

    expect(result.success).toBe(true)
    if (!result.success) {
      throw new Error("Expected workspace model settings to parse")
    }
    expect(result.data.agent_enabled_model_refs).toEqual([])
  })
})
