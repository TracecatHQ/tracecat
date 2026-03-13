import {
  buildRuntimeSettingsUpdate,
  runtimeSettingsSchema,
} from "@/components/settings/workspace-runtime-settings"

describe("workspace runtime settings", () => {
  it("sends null when clearing the default timeout", () => {
    expect(
      buildRuntimeSettingsUpdate({
        workflow_unlimited_timeout_enabled: false,
        workflow_default_timeout_seconds: undefined,
      })
    ).toEqual({
      workflow_unlimited_timeout_enabled: false,
      workflow_default_timeout_seconds: null,
    })
  })

  it("keeps numeric timeout values when present", () => {
    expect(
      buildRuntimeSettingsUpdate({
        workflow_unlimited_timeout_enabled: false,
        workflow_default_timeout_seconds: 300,
      })
    ).toEqual({
      workflow_unlimited_timeout_enabled: false,
      workflow_default_timeout_seconds: 300,
    })
  })

  it("accepts valid timeout values", () => {
    const result = runtimeSettingsSchema.safeParse({
      workflow_unlimited_timeout_enabled: false,
      workflow_default_timeout_seconds: 300,
    })

    expect(result.success).toBe(true)
  })
})
