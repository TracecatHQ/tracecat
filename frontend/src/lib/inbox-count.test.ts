import { formatPendingInboxCount } from "@/lib/inbox-count"

describe("formatPendingInboxCount", () => {
  it("formats counts up to the badge maximum", () => {
    expect(formatPendingInboxCount(42)).toBe("42")
  })

  it("caps large counts", () => {
    expect(formatPendingInboxCount(100)).toBe("99+")
  })
})
