import {
  isValidSqlIntegerInput,
  isValidSqlNumericInput,
} from "@/lib/sql-value-validation"

describe("sql value validation", () => {
  it("accepts backend-compatible integer inputs", () => {
    expect(isValidSqlIntegerInput("42")).toBe(true)
    expect(isValidSqlIntegerInput("  -7 ")).toBe(true)
    expect(isValidSqlIntegerInput("+8")).toBe(true)
  })

  it("rejects integer inputs the backend would not coerce as INTEGER", () => {
    expect(isValidSqlIntegerInput("1.5")).toBe(false)
    expect(isValidSqlIntegerInput("1e3")).toBe(false)
    expect(isValidSqlIntegerInput("0x10")).toBe(false)
    expect(isValidSqlIntegerInput("")).toBe(false)
  })

  it("accepts backend-compatible numeric inputs", () => {
    expect(isValidSqlNumericInput("42")).toBe(true)
    expect(isValidSqlNumericInput("1.5")).toBe(true)
    expect(isValidSqlNumericInput(".5")).toBe(true)
    expect(isValidSqlNumericInput("1.")).toBe(true)
    expect(isValidSqlNumericInput("1e3")).toBe(true)
    expect(isValidSqlNumericInput("+1.2e-3")).toBe(true)
  })

  it("rejects numeric inputs accepted by JavaScript Number but rejected by the backend", () => {
    expect(isValidSqlNumericInput("0x10")).toBe(false)
    expect(isValidSqlNumericInput("0b10")).toBe(false)
    expect(isValidSqlNumericInput("NaN")).toBe(false)
    expect(isValidSqlNumericInput("Infinity")).toBe(false)
    expect(isValidSqlNumericInput(".")).toBe(false)
    expect(isValidSqlNumericInput("1 / 2")).toBe(false)
  })
})
