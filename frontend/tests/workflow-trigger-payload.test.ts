import {
  DEFAULT_TRIGGER_PAYLOAD,
  parseTriggerPayload,
  readPersistedTriggerPayload,
  triggerPayloadStorageKey,
  validateTriggerPayload,
  writePersistedTriggerPayload,
} from "@/lib/workflow-trigger-payload"

describe("workflow-trigger-payload", () => {
  const scope = {
    userId: "user-123",
    workspaceId: "workspace-123",
    workflowId: "workflow-123",
  }

  beforeEach(() => {
    window.localStorage.clear()
  })

  describe("DEFAULT_TRIGGER_PAYLOAD", () => {
    it("defaults to an empty JSON object", () => {
      expect(DEFAULT_TRIGGER_PAYLOAD).toBe("{}")
    })
  })

  describe("trigger payload persistence", () => {
    it("stores payloads in localStorage instead of cookies", () => {
      const largePayload = JSON.stringify({
        message: "x".repeat(10_000),
      })

      writePersistedTriggerPayload({
        ...scope,
        triggerPayload: largePayload,
      })

      expect(window.localStorage.getItem(triggerPayloadStorageKey(scope))).toBe(
        largePayload
      )
      expect(readPersistedTriggerPayload(scope)).toBe(largePayload)
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
