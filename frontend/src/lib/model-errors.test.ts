import {
  AGENT_SETTINGS_HREF,
  describeModelError,
  modelErrorFacts,
  parseModelErrorDetail,
} from "@/lib/model-errors"

describe("parseModelErrorDetail", () => {
  it("keeps known fields and drops non-string extras", () => {
    expect(
      parseModelErrorDetail({
        code: "provider_auth_failed",
        provider: "openai",
        model: "gpt-test",
        status_code: 401,
        request_id: "req_1",
        provider_error_code: 7,
      })
    ).toEqual({
      code: "provider_auth_failed",
      kind: null,
      provider: "openai",
      model: "gpt-test",
      status_code: 401,
      request_id: "req_1",
      provider_error_code: null,
    })
  })

  it("reads a code from a newer server as unknown", () => {
    expect(parseModelErrorDetail({ code: "brand_new" })?.code).toBe("unknown")
  })

  it("rejects payloads without a code", () => {
    expect(parseModelErrorDetail(null)).toBeNull()
    expect(parseModelErrorDetail({ provider: "openai" })).toBeNull()
  })
})

describe("describeModelError", () => {
  it("names the provider and model for a rejected key", () => {
    const copy = describeModelError({
      code: "provider_auth_failed",
      provider: "openai",
      model: "gpt-test",
    })
    expect(copy.title).toBe("OpenAI rejected the API key for gpt-test.")
    expect(copy.settingsHref).toBe(AGENT_SETTINGS_HREF)
  })

  it("needs no settings link for a rate limit", () => {
    const copy = describeModelError({
      code: "rate_limited",
      provider: "anthropic",
      model: "claude-test",
    })
    expect(copy.title).toContain("Anthropic")
    expect(copy.settingsHref).toBeUndefined()
  })

  it("falls back to generic copy", () => {
    expect(describeModelError({ code: "unknown" }).title).toBe(
      "The request to the selected model failed."
    )
  })
})

describe("modelErrorFacts", () => {
  it("lists status and request id for support", () => {
    expect(
      modelErrorFacts({
        code: "provider_auth_failed",
        status_code: 401,
        request_id: "req_1",
      })
    ).toEqual(["code: provider_auth_failed", "HTTP 401", "request ID: req_1"])
  })
})
