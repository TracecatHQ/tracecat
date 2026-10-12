import {
  getFormErrorCode,
  getFormErrorDetail,
  getFormLoadFailure,
  isFormOfflineError,
  mapFormFieldErrors,
} from "@/lib/forms/errors"

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
    expect(getFormErrorCode(apiError(409, detail))).toBe("form_offline")
  })

  it.each([
    ["a string detail", apiError(404, "Form not found")],
    ["a request validation error", apiError(422, [{ loc: ["x"], msg: "y" }])],
    ["a plain error", new Error("boom")],
    ["nothing", undefined],
  ])("returns null for %s", (_name, error) => {
    expect(getFormErrorDetail(error)).toBeNull()
    expect(getFormErrorCode(error)).toBeNull()
  })
})

describe("getFormLoadFailure", () => {
  it.each([403, 404, 422])("treats %d as not found", (status) => {
    expect(getFormLoadFailure(apiError(status, "Form not found"))).toBe(
      "not_found"
    )
  })

  it.each(["form_offline", "form_unavailable", "form_workflow_unpublished"])(
    "treats %s as offline",
    (code) => {
      const error = apiError(409, { code, message: "x" })
      expect(isFormOfflineError(error)).toBe(true)
      expect(getFormLoadFailure(error)).toBe("offline")
    }
  )

  it("treats everything else as a retryable error", () => {
    expect(getFormLoadFailure(apiError(500, "Internal error"))).toBe("error")
    expect(getFormLoadFailure(new Error("Network Error"))).toBe("error")
    expect(
      isFormOfflineError(apiError(409, { code: "form_changed", message: "x" }))
    ).toBe(false)
  })
})

describe("mapFormFieldErrors", () => {
  it("splits errors into visible fields and form-level messages", () => {
    expect(
      mapFormFieldErrors(
        [
          { field: "model", code: "invalid_option", message: "Choose one" },
          { field: "ghost", code: "unknown_field", message: "Not a field" },
        ],
        ["model", "notes"]
      )
    ).toEqual({
      fieldErrors: { model: "Choose one" },
      formErrors: ["ghost: Not a field"],
    })
  })

  it("keeps the last error when a field has several", () => {
    expect(
      mapFormFieldErrors(
        [
          { field: "quote", code: "required", message: "Required" },
          {
            field: "quote",
            code: "invalid_file",
            message: "Upload the file again",
          },
        ],
        ["quote"]
      ).fieldErrors
    ).toEqual({ quote: "Upload the file again" })
  })

  it("handles a response with no errors", () => {
    expect(mapFormFieldErrors(null, ["a"])).toEqual({
      fieldErrors: {},
      formErrors: [],
    })
  })
})
