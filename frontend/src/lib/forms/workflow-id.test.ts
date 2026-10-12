import { workflowShortIdToUuid } from "@/lib/forms/workflow-id"

describe("workflowShortIdToUuid", () => {
  // Pairs produced by the backend's `WorkflowUUID.short()`.
  it.each([
    ["wf_3KbAYlkEEToril859NNEOb", "7b0c1f6e-3d2a-4e5b-9c8d-1a2b3c4d5e6f"],
    ["wf_000000001vGeH72LxVtxKh", "00000000-0000-4000-8000-000000000001"],
  ])("converts %s", (shortId, uuid) => {
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
