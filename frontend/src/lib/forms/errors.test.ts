import { getFormErrorDetail, getFormLoadFailure } from "@/lib/forms/errors"

function apiError(status: number, detail: unknown) {
  return Object.assign(new Error("Request failed"), {
    status,
    body: { detail },
  })
}

describe("getFormErrorDetail", () => {
  it("reads a typed forms error", () => {
    const detail = { code: "form_offline", message: "This form is offline" }
    expect(getFormErrorDetail(apiError(409, detail))).toEqual(detail)
  })

  it.each([
    ["a string detail", apiError(404, "Form not found")],
    ["a request validation error", apiError(422, [{ loc: ["x"], msg: "y" }])],
    ["a plain error", new Error("boom")],
    ["nothing", undefined],
  ])("returns null for %s", (_name, error) => {
    expect(getFormErrorDetail(error)).toBeNull()
  })
})

// The requester page tests cover 403, 404, `form_offline` and a 500.
describe("getFormLoadFailure", () => {
  const coded = (code: string) => apiError(409, { code, message: "x" })

  it.each([
    ["an ID that is not a UUID", apiError(422, []), "not_found"],
    ["form_unavailable", coded("form_unavailable"), "offline"],
    [
      "form_workflow_unpublished",
      coded("form_workflow_unpublished"),
      "offline",
    ],
    ["form_changed", coded("form_changed"), "error"],
    ["a network failure", new Error("Network Error"), "error"],
  ])("classifies %s", (_name, error, expected) => {
    expect(getFormLoadFailure(error)).toBe(expected)
  })
})
