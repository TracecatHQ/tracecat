import {
  formWorkflowHref,
  workflowShortIdToUuid,
  workflowUuidToShortId,
} from "@/lib/forms/workflow-id"

// Pairs produced by the backend's `WorkflowUUID.short()`.
const PAIRS = [
  ["wf_3KbAYlkEEToril859NNEOb", "7b0c1f6e-3d2a-4e5b-9c8d-1a2b3c4d5e6f"],
  ["wf_000000001vGeH72LxVtxKh", "00000000-0000-4000-8000-000000000001"],
] as const

describe("workflowUuidToShortId", () => {
  it.each(PAIRS)("produces %s", (shortId, uuid) => {
    expect(workflowUuidToShortId(uuid)).toBe(shortId)
    expect(workflowUuidToShortId(uuid.toUpperCase())).toBe(shortId)
  })

  it.each(["wf_3KbAYlkEEToril859NNEOb", "not-a-uuid", ""])(
    "returns null for %j",
    (value) => {
      expect(workflowUuidToShortId(value)).toBeNull()
    }
  )
})

describe("formWorkflowHref", () => {
  it("links to the builder by the workflow's short ID", () => {
    expect(formWorkflowHref("workspace-1", PAIRS[0][1])).toBe(
      "/workspaces/workspace-1/workflows/wf_3KbAYlkEEToril859NNEOb"
    )
  })

  it("passes through an ID that is not a UUID", () => {
    expect(formWorkflowHref("workspace-1", PAIRS[0][0])).toBe(
      "/workspaces/workspace-1/workflows/wf_3KbAYlkEEToril859NNEOb"
    )
  })
})

describe("workflowShortIdToUuid", () => {
  it.each(PAIRS)("converts %s", (shortId, uuid) => {
    expect(workflowShortIdToUuid(shortId)).toBe(uuid)
  })

  it.each([
    "7b0c1f6e-3d2a-4e5b-9c8d-1a2b3c4d5e6f",
    "wf_not-base62!",
    "wf_ZZZZZZZZZZZZZZZZZZZZZZZZZZZZ",
    "",
  ])("returns null for %j", (value) => {
    expect(workflowShortIdToUuid(value)).toBeNull()
  })
})
