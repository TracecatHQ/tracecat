import { parseEmailList } from "@/lib/email-parse"

describe("parseEmailList", () => {
  it("splits on commas, spaces, semicolons, and newlines", () => {
    const { valid } = parseEmailList(
      "a@example.com, b@example.com c@example.com;d@example.com\ne@example.com"
    )
    expect(valid).toEqual([
      "a@example.com",
      "b@example.com",
      "c@example.com",
      "d@example.com",
      "e@example.com",
    ])
  })

  it("trims and lowercases", () => {
    const { valid } = parseEmailList("  Foo@Example.COM  ")
    expect(valid).toEqual(["foo@example.com"])
  })

  it("deduplicates case-insensitively", () => {
    const { valid } = parseEmailList("dup@example.com, DUP@example.com")
    expect(valid).toEqual(["dup@example.com"])
  })

  it("separates invalid addresses", () => {
    const { valid, invalid } = parseEmailList(
      "good@example.com, not-an-email, also bad@@x"
    )
    expect(valid).toEqual(["good@example.com"])
    expect(invalid).toEqual(["not-an-email", "also", "bad@@x"])
  })

  it("returns empty arrays for blank input", () => {
    expect(parseEmailList("   \n  ")).toEqual({ valid: [], invalid: [] })
  })
})
