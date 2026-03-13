import {
  DEFAULT_TRIGGER_PAYLOAD,
  parseTriggerPayload,
  validateTriggerPayload,
} from "@/lib/workflow-trigger-payload"

describe("workflow-trigger-payload", () => {
  describe("DEFAULT_TRIGGER_PAYLOAD", () => {
    it("defaults to an empty JSON object", () => {
      expect(DEFAULT_TRIGGER_PAYLOAD).toBe("{}")
    })
  })

  describe("validateTriggerPayload", () => {
    it("accepts valid JSON", () => {
      expect(validateTriggerPayload('{"foo":"bar"}')).toBeNull()
    })

    it("accepts blank payloads", () => {
      expect(validateTriggerPayload("   ")).toBeNull()
    })

    it("returns a readable error for invalid JSON", () => {
      expect(validateTriggerPayload("{")).toContain("Invalid JSON format")
    })
  })

  describe("parseTriggerPayload", () => {
    it("parses valid JSON values", () => {
      expect(parseTriggerPayload('{"foo":"bar"}')).toEqual({ foo: "bar" })
    })

    it("returns undefined for blank payloads", () => {
      expect(parseTriggerPayload(" ")).toBeUndefined()
    })
  })
})
