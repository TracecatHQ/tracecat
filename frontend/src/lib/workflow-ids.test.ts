import { workflowUuidToShortId } from "@/lib/workflow-ids"

describe("workflowUuidToShortId", () => {
  // Expected values from tracecat.identifiers.common.id_to_short.
  it.each([
    ["00000000-0000-0000-0000-000000000001", "wf_0000000000000000000001"],
    ["ffffffff-ffff-ffff-ffff-ffffffffffff", "wf_7N42dgm5tFLK9N8MT7fHC7"],
    ["6f1c2a7e-3b4d-4e5f-9a1b-2c3d4e5f6a7b", "wf_3nF1fBrbY6AIX7Mr2qhEpR"],
  ])("encodes %s like the backend", (uuid, shortId) => {
    expect(workflowUuidToShortId(uuid)).toBe(shortId)
  })

  it("accepts a UUID without dashes", () => {
    expect(workflowUuidToShortId("6f1c2a7e3b4d4e5f9a1b2c3d4e5f6a7b")).toBe(
      "wf_3nF1fBrbY6AIX7Mr2qhEpR"
    )
  })

  it("leaves a value that is not a UUID unchanged", () => {
    expect(workflowUuidToShortId("wf_3nF1fBrbY6AIX7Mr2qhEpR")).toBe(
      "wf_3nF1fBrbY6AIX7Mr2qhEpR"
    )
  })
})
