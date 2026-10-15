import { generalSettingsSchema } from "@/components/settings/workspace-general-settings"

describe("workspace general settings", () => {
  it("trims the workspace name during validation", () => {
    const result = generalSettingsSchema.safeParse({
      name: "  Incident Response  ",
      chat_verbosity: "milestones",
    })

    expect(result.success).toBe(true)
    if (!result.success) {
      throw new Error("Expected a trimmed workspace name to pass validation")
    }
    expect(result.data.name).toBe("Incident Response")
  })

  it("rejects a workspace name that is empty after trimming", () => {
    const result = generalSettingsSchema.safeParse({
      name: "   ",
      chat_verbosity: "milestones",
    })

    expect(result.success).toBe(false)
  })

  it("rejects an unknown chat verbosity", () => {
    const result = generalSettingsSchema.safeParse({
      name: "Incident Response",
      chat_verbosity: "verbose",
    })

    expect(result.success).toBe(false)
  })
})
