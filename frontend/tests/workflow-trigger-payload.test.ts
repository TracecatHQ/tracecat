import Cookies from "js-cookie"
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
    Cookies.remove(
      `__tracecat:builder:trigger-payload:${scope.userId}:${scope.workspaceId}:${scope.workflowId}`,
      {
        path: "/",
      }
    )
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
      expect(
        Cookies.get(
          `__tracecat:builder:trigger-payload:${scope.userId}:${scope.workspaceId}:${scope.workflowId}`
        )
      ).toBeUndefined()
    })

    it("migrates legacy cookie values into localStorage", () => {
      const legacyPayload = '{"legacy":true}'
      Cookies.set(
        `__tracecat:builder:trigger-payload:${scope.userId}:${scope.workspaceId}:${scope.workflowId}`,
        legacyPayload,
        {
          path: "/",
        }
      )

      expect(readPersistedTriggerPayload(scope)).toBe(legacyPayload)
      expect(window.localStorage.getItem(triggerPayloadStorageKey(scope))).toBe(
        legacyPayload
      )
      expect(
        Cookies.get(
          `__tracecat:builder:trigger-payload:${scope.userId}:${scope.workspaceId}:${scope.workflowId}`
        )
      ).toBeUndefined()
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
