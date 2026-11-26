import { describeTriggerError } from "@/components/triggers/trigger-errors"

function apiError(status: number, body: unknown): Error {
  return Object.assign(new Error("Request failed"), { status, body })
}

describe("describeTriggerError", () => {
  it("names a missing execute scope in plain words", () => {
    const error = apiError(403, {
      error: {
        code: "insufficient_scope",
        missing_scopes: ["workflow:execute"],
      },
    })
    expect(describeTriggerError(error)).toBe(
      "You don't have permission to run workflows (workflow:execute). Ask a workspace admin for access."
    )
  })

  it("explains a 403 without scope details", () => {
    expect(describeTriggerError(apiError(403, { detail: "Forbidden" }))).toBe(
      "You don't have permission to do this in this workspace."
    )
  })

  it("passes through the API detail for other errors", () => {
    const error = apiError(409, { detail: "A source already reads this topic" })
    expect(describeTriggerError(error)).toBe(
      "A source already reads this topic"
    )
  })
})
