import { ApiError } from "@/client"
import { describeApiError } from "@/hooks/use-secret-stores"

function validationError(detail: unknown) {
  return new ApiError(
    { method: "POST", url: "/organization/secret-stores" },
    {
      url: "/organization/secret-stores",
      ok: false,
      status: 422,
      statusText: "Unprocessable Content",
      body: { detail },
    },
    "Validation Error"
  )
}

describe("describeApiError", () => {
  it("hides raw regex patterns from validation errors", () => {
    const message = describeApiError(
      validationError([
        {
          type: "string_pattern_mismatch",
          loc: ["body", "config", "role_arn"],
          msg: "String should match pattern '^arn:aws(?:-[a-z]+)*:iam::\\d{12}:role/[\\w+=,.@/-]+$'",
          ctx: { pattern: "^arn:aws" },
        },
      ])
    )
    expect(message).toBe("Role ARN is not in the expected format.")
  })

  it("keeps model validator messages readable", () => {
    const message = describeApiError(
      validationError([
        {
          type: "value_error",
          loc: ["body", "config"],
          msg: "Value error, Role ARN partition must be 'aws-cn' for region 'cn-north-1'",
          ctx: {},
        },
      ])
    )
    expect(message).toBe(
      "Role ARN partition must be 'aws-cn' for region 'cn-north-1'"
    )
  })
})
