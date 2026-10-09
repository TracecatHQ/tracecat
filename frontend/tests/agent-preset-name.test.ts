import { agentPresetSlug, uniqueAgentPresetName } from "@/lib/agent-preset-name"

describe("uniqueAgentPresetName", () => {
  it("keeps a free name", () => {
    expect(uniqueAgentPresetName("Alert Triage", ["other"])).toBe(
      "Alert Triage"
    )
  })

  it("numbers a taken name until its slug is free", () => {
    expect(
      uniqueAgentPresetName("Alert Triage", ["alert-triage", "alert-triage-2"])
    ).toBe("Alert Triage 3")
  })

  it("compares by slug, not display text", () => {
    expect(agentPresetSlug("SOC 2 Report Review")).toBe("soc-2-report-review")
    expect(
      uniqueAgentPresetName("soc 2 report review", ["soc-2-report-review"])
    ).toBe("soc 2 report review 2")
  })
})

describe("agentPresetSlug", () => {
  it("folds accents like the backend slugger", () => {
    expect(agentPresetSlug("Naïve Café")).toBe("naive-cafe")
  })
})

describe("uniqueAgentPresetName length", () => {
  it("keeps a numbered name within 120 characters", () => {
    const name = "a".repeat(120)
    const unique = uniqueAgentPresetName(name, [agentPresetSlug(name)])
    expect(unique).toBe(`${"a".repeat(118)} 2`)
  })
})
