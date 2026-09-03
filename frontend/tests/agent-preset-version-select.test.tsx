import {
  formatAgentPresetVersionLabel,
  getAgentPresetVersionFallbackLabel,
} from "@/components/agents/agent-preset-version-select"

describe("agent preset version select labels", () => {
  it("uses a non-current fallback for pinned versions when the version number is missing", () => {
    expect(
      getAgentPresetVersionFallbackLabel({
        currentVersionId: "current-version",
        selectedVersionId: "pinned-version",
      })
    ).toBe("Pinned version")
  })

  it("keeps the aria label non-current for pinned versions when the version number is missing", () => {
    expect(
      formatAgentPresetVersionLabel({
        currentVersionId: "current-version",
        selectedVersionId: "pinned-version",
        selectedVersionNumber: null,
      })
    ).toBe("Version")
  })

  it("keeps current fallback labels for the current version paths", () => {
    expect(
      getAgentPresetVersionFallbackLabel({
        currentVersionId: "current-version",
        selectedVersionId: null,
      })
    ).toBe("Current")

    expect(
      getAgentPresetVersionFallbackLabel({
        currentVersionId: "current-version",
        selectedVersionId: "current-version",
      })
    ).toBe("Current version")
  })
})
