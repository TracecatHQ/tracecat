import {
  chainError,
  getApiErrorDetail,
  showFallbackErrorToast,
} from "@/lib/errors"

const mockToast = jest.fn()

jest.mock("@/components/ui/use-toast", () => ({
  toast: (...args: unknown[]) => mockToast(...args),
}))

describe("getApiErrorDetail", () => {
  it("returns string detail when present", () => {
    const error = Object.assign(new Error("Bad Request"), {
      body: { detail: "Registry sync validation failed", message: null },
    })

    expect(getApiErrorDetail(error)).toBe("Registry sync validation failed")
  })

  it("prefers the API body message when detail is null", () => {
    const error = Object.assign(new Error("Bad Request"), {
      body: {
        detail: null,
        message: "Registry sync validation failed with 2 error(s).",
      },
    })

    expect(getApiErrorDetail(error)).toBe(
      "Registry sync validation failed with 2 error(s)."
    )
  })

  it("serializes structured detail before falling back to the Error text", () => {
    const error = Object.assign(new Error("Bad Request"), {
      body: {
        detail: {
          action: "tracecat.examples.broken",
          reason: "Action not found",
        },
        message: "Internal Server Error",
      },
    })

    expect(getApiErrorDetail(error)).toBe(
      JSON.stringify({
        action: "tracecat.examples.broken",
        reason: "Action not found",
      })
    )
  })

  it("shows every validation message with its file path without JSON metadata", () => {
    const error = Object.assign(new Error("Bad Request"), {
      body: {
        detail: {
          code: "skill_draft_tool_validation_failed",
          errors: [
            {
              code: "unknown_skill_tools",
              path: "SKILL.md",
              message: "Tool not found.",
            },
            {
              code: "invalid_path",
              path: "notes.txt",
              message: "File is invalid.",
            },
            null,
            { message: 42 },
            { message: " " },
          ],
        },
      },
    })

    expect(getApiErrorDetail(error)).toBe(
      "SKILL.md: Tool not found.\nnotes.txt: File is invalid."
    )
  })

  it("reads request validation arrays and structured message details", () => {
    const error = Object.assign(new Error("Unprocessable Entity"), {
      body: {
        detail: [
          { loc: ["body", "name"], msg: "Name is required.", type: "missing" },
        ],
      },
    })
    expect(getApiErrorDetail(error)).toBe("Name is required.")

    const messageError = Object.assign(new Error("Bad Request"), {
      body: {
        detail: {
          code: "invalid_skill",
          message: "Fix the skill before publishing.",
        },
      },
    })
    expect(getApiErrorDetail(messageError)).toBe(
      "Fix the skill before publishing."
    )
  })

  it("preserves the fallback for structured details without readable messages", () => {
    const detail = { code: "invalid_skill", errors: [{ message: null }] }
    const error = Object.assign(new Error("Bad Request"), { body: { detail } })
    expect(getApiErrorDetail(error)).toBe(JSON.stringify(detail))
  })
})

describe("chainError", () => {
  beforeEach(() => {
    mockToast.mockClear()
  })

  it("handles globally matched errors before invoking the local handler", () => {
    const local = jest.fn()
    const error = Object.assign(new Error("Forbidden"), {
      status: 403,
      body: { detail: "Missing workspace:read scope" },
    })

    chainError(local)(error, "workflow-123", undefined, { client: true })

    expect(local).toHaveBeenCalledWith(error, "workflow-123", undefined, {
      client: true,
    })
    expect(mockToast).toHaveBeenCalledTimes(1)
    expect(mockToast).toHaveBeenCalledWith({
      title: "Permission denied",
      description: "Missing workspace:read scope",
      variant: "destructive",
    })
    expect(mockToast.mock.invocationCallOrder[0]).toBeLessThan(
      local.mock.invocationCallOrder[0]
    )
  })

  it("forwards unmatched errors and all callback arguments locally", () => {
    const local = jest.fn()
    const error = Object.assign(new Error("Conflict"), {
      status: 409,
      body: { detail: "Workflow already exists" },
    })
    const context = { client: true }

    chainError(local)(error, "workflow-123", undefined, context)

    expect(local).toHaveBeenCalledWith(
      error,
      "workflow-123",
      undefined,
      context
    )
    expect(mockToast).not.toHaveBeenCalled()
  })

  it("shows the fallback when a mutation has no local handler", () => {
    const error = Object.assign(new Error("Internal Server Error"), {
      status: 500,
      body: "upstream failure",
    })

    chainError()(error)

    expect(mockToast).toHaveBeenCalledTimes(1)
    expect(mockToast).toHaveBeenCalledWith({
      description: "Internal Server Error",
      variant: "destructive",
    })
  })
})

describe("showFallbackErrorToast", () => {
  it("never renders undefined when the thrown value has no message", () => {
    mockToast.mockClear()

    showFallbackErrorToast(undefined)

    expect(mockToast).toHaveBeenCalledWith({
      description: "Please try again.",
      variant: "destructive",
    })
  })
})
