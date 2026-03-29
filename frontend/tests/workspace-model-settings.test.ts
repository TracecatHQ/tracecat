import {
  buildWorkspaceModelSettingsUpdate,
  workspaceModelSettingsSchema,
} from "@/components/settings/workspace-model-settings"

describe("workspace model settings", () => {
  it("clears the workspace subset when inheriting organization models", () => {
    expect(
      buildWorkspaceModelSettingsUpdate({
        inherit_all: true,
        models: [
          {
            source_id: null,
            model_provider: "openai",
            model_name: "gpt-4.1",
          },
        ],
      })
    ).toBeNull()
  })

  it("keeps the explicit workspace subset when inheritance is disabled", () => {
    expect(
      buildWorkspaceModelSettingsUpdate({
        inherit_all: false,
        models: [
          {
            source_id: "source-123",
            model_provider: "anthropic",
            model_name: "claude-sonnet-4",
          },
        ],
      })
    ).toEqual([
      {
        model_name: "claude-sonnet-4",
        model_provider: "anthropic",
        source_id: "source-123",
      },
    ])
  })

  it("rejects an empty explicit subset", () => {
    const result = workspaceModelSettingsSchema.safeParse({
      inherit_all: false,
      models: [],
    })

    expect(result.success).toBe(false)
  })

  it("defaults an omitted model list while inheriting", () => {
    const result = workspaceModelSettingsSchema.safeParse({
      inherit_all: true,
    })

    expect(result.success).toBe(true)
    if (!result.success) {
      throw new Error("Expected inherited workspace model settings to parse")
    }
    expect(result.data.models).toEqual([])
  })
})
