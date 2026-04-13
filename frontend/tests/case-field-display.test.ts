import {
  formatCaseFieldDisplayLabel,
  formatCaseFieldNumericDisplayValue,
  getCaseFieldEditorValue,
} from "@/lib/case-field-display"

describe("case field display formatting", () => {
  it("rounds numeric values for display without float artifacts", () => {
    expect(
      formatCaseFieldNumericDisplayValue("123.299999999999997157829056")
    ).toBe("123.3")
    expect(formatCaseFieldNumericDisplayValue(1.23456)).toBe("1.23456")
  })

  it("preserves exact short decimal strings", () => {
    expect(formatCaseFieldNumericDisplayValue("1.30")).toBe("1.30")
    expect(getCaseFieldEditorValue("1.30", "NUMERIC")).toBe("1.30")
  })

  it("formats numeric field labels without changing text fields", () => {
    expect(
      formatCaseFieldDisplayLabel("123.299999999999997157829056", "NUMERIC")
    ).toBe("123.3")
    expect(formatCaseFieldDisplayLabel("00123.4500", "TEXT")).toBe("00123.4500")
  })

  it("normalizes editor values for numeric fields", () => {
    expect(
      getCaseFieldEditorValue("123.299999999999997157829056", "NUMERIC")
    ).toBe("123.299999999999997157829056")
    expect(getCaseFieldEditorValue(7, "INTEGER")).toBe("7")
  })

  it("formats booleans and labeled objects for badges", () => {
    expect(formatCaseFieldDisplayLabel(true)).toBe("Yes")
    expect(
      formatCaseFieldDisplayLabel({
        label: "Apple",
        url: "https://example.com",
      })
    ).toBe("Apple")
  })
})
