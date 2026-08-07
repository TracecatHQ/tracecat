import { parseMentionHref } from "@/lib/mentions"

const PRESET_ID = "0f9d9f4c-1c2b-4f3a-9a1e-2b7c8d9e0f11"

describe("parseMentionHref", () => {
  it("parses an agent mention", () => {
    expect(parseMentionHref(`mention://agent/${PRESET_ID}`)).toEqual({
      type: "agent",
      presetId: PRESET_ID,
    })
  })

  it("accepts uppercase uuids and preserves the original casing", () => {
    const upper = PRESET_ID.toUpperCase()
    expect(parseMentionHref(`mention://agent/${upper}`)).toEqual({
      type: "agent",
      presetId: upper,
    })
  })

  it.each([
    ["a plain https url", `https://example.test/agent/${PRESET_ID}`],
    ["a relative url", "/agents/some-preset"],
    ["a non-uuid id", "mention://agent/not-a-uuid"],
    ["a numeric id", "mention://agent/12345"],
    ["an unknown segment", `mention://user/${PRESET_ID}`],
    ["an unknown segment", `mention://foo/${PRESET_ID}`],
    ["a missing id", "mention://agent"],
    ["an empty id", "mention://agent/"],
    ["extra segments", `mention://agent/${PRESET_ID}/extra`],
    ["a missing segment", `mention://${PRESET_ID}`],
    ["a scheme-only href", "mention://"],
    ["a wrong scheme", `mentions://agent/${PRESET_ID}`],
    ["an empty string", ""],
  ])("returns null for %s", (_description, href) => {
    expect(parseMentionHref(href)).toBeNull()
  })

  it("returns null for null and undefined", () => {
    expect(parseMentionHref(null)).toBeNull()
    expect(parseMentionHref(undefined)).toBeNull()
  })

  it("never throws on arbitrary input", () => {
    const inputs = [
      "mention:///",
      "mention://///",
      "mention://agent/%%%",
      "mention://agent/../../etc/passwd",
      "mention://AGENT/" + PRESET_ID,
      "  mention://agent/" + PRESET_ID,
      "\u0000",
      "a".repeat(10_000),
    ]
    for (const input of inputs) {
      expect(() => parseMentionHref(input)).not.toThrow()
      expect(parseMentionHref(input)).toBeNull()
    }
  })
})
