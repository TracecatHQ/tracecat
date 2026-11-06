import { CASE_PANEL_KEYS, CASE_PANELS } from "@/components/cases/case-panels"

describe("CASE_PANELS", () => {
  it("numbers digit shortcuts in display order", () => {
    expect(CASE_PANELS.map((panel) => panel.shortcut)).toEqual(
      CASE_PANELS.map((_, index) => index + 1)
    )
  })

  it("lists panels in CASE_PANEL_KEYS order", () => {
    expect(CASE_PANELS.map((panel) => panel.key)).toEqual([...CASE_PANEL_KEYS])
  })
})
