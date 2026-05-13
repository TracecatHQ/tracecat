import { formatPendingApprovalCount } from "@/lib/approvals"

describe("formatPendingApprovalCount", () => {
  it("formats counts up to the badge maximum", () => {
    expect(formatPendingApprovalCount(42)).toBe("42")
  })

  it("caps large counts", () => {
    expect(formatPendingApprovalCount(100)).toBe("99+")
  })
})
