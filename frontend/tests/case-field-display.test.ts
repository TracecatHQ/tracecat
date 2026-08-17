import {
  createCaseFieldDisplayNameMap,
  createCaseFieldReference,
  formatCaseFieldDisplayLabel,
  formatCaseFieldNumericDisplayValue,
  getCaseFieldEditorValue,
  isCustomFieldValueEmpty,
  orderCustomFieldsForDisplay,
} from "@/lib/case-field-display"

describe("case field references", () => {
  it("derives a snake_case reference from a display name", () => {
    expect(createCaseFieldReference("Analyst Verdict")).toBe("analyst_verdict")
    expect(createCaseFieldReference("Résumé / Outcome")).toBe("resume_outcome")
  })

  it("prefixes references derived from names that begin with a number", () => {
    expect(createCaseFieldReference("2FA status")).toBe("field_2fa_status")
    expect(createCaseFieldReference("2026")).toBe("field_2026")
  })

  it("returns an empty reference when no characters can be normalized", () => {
    expect(createCaseFieldReference("处理结果 🚨")).toBe("")
  })

  it("caps references at the API's column-name limit", () => {
    expect(createCaseFieldReference("a".repeat(120))).toHaveLength(100)
    expect(createCaseFieldReference(`2${"a".repeat(120)}`)).toHaveLength(100)
  })
})

describe("case field display formatting", () => {
  it("indexes friendly names by stable field reference", () => {
    const displayNames = createCaseFieldDisplayNameMap([
      { id: "analyst_verdict_v2", display_name: "Final determination" },
    ])

    expect(displayNames.get("analyst_verdict_v2")).toBe("Final determination")
    expect(displayNames.has("unknown_reference")).toBe(false)
  })

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

describe("isCustomFieldValueEmpty", () => {
  it("treats null, blank strings, and empty containers as empty", () => {
    expect(isCustomFieldValueEmpty(null)).toBe(true)
    expect(isCustomFieldValueEmpty(undefined)).toBe(true)
    expect(isCustomFieldValueEmpty("")).toBe(true)
    expect(isCustomFieldValueEmpty("   ")).toBe(true)
    expect(isCustomFieldValueEmpty([])).toBe(true)
    expect(isCustomFieldValueEmpty({})).toBe(true)
  })

  it("treats booleans and real values as non-empty", () => {
    expect(isCustomFieldValueEmpty(false)).toBe(false)
    expect(isCustomFieldValueEmpty(true)).toBe(false)
    expect(isCustomFieldValueEmpty(0)).toBe(false)
    expect(isCustomFieldValueEmpty("text")).toBe(false)
    expect(isCustomFieldValueEmpty(["a"])).toBe(false)
    expect(isCustomFieldValueEmpty({ a: 1 })).toBe(false)
  })
})

describe("orderCustomFieldsForDisplay", () => {
  // Interleaved so the partition assertions below are not vacuous: empties
  // (alpha, charlie, echo) alternate with non-empties (bravo, delta, foxtrot),
  // and `delta` holds `false` so booleans exercise the non-empty path.
  const fields = [
    { id: "alpha", value: "" },
    { id: "bravo", value: "filled" },
    { id: "charlie", value: null },
    { id: "delta", value: false },
    { id: "echo", value: [] },
    { id: "foxtrot", value: { key: "value" } },
  ]

  it("collapsed shows only non-empty fields in their original order", () => {
    expect(orderCustomFieldsForDisplay(fields, false).map((f) => f.id)).toEqual(
      ["bravo", "delta", "foxtrot"]
    )
  })

  it("expanded puts non-empty fields first, then empty ones, stably", () => {
    expect(orderCustomFieldsForDisplay(fields, true).map((f) => f.id)).toEqual([
      "bravo",
      "delta",
      "foxtrot",
      "alpha",
      "charlie",
      "echo",
    ])
  })

  it("treats whitespace-only strings and empty objects as empty", () => {
    const sparse = [
      { id: "ws", value: "   " },
      { id: "kept", value: "x" },
      { id: "obj", value: {} },
    ]

    expect(orderCustomFieldsForDisplay(sparse, false).map((f) => f.id)).toEqual(
      ["kept"]
    )
    expect(orderCustomFieldsForDisplay(sparse, true).map((f) => f.id)).toEqual([
      "kept",
      "ws",
      "obj",
    ])
  })
})
