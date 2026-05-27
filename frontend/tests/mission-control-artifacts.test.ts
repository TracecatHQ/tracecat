import type { UIMessage } from "ai"
import { artifactKey, getArtifactDataPayload } from "@/types/mission-control"

describe("mission control artifacts", () => {
  it("parses data-artifact UI message parts", () => {
    const [part] = [
      {
        type: "data-artifact",
        data: {
          op: "upsert",
          artifact: {
            type: "case",
            id: "case-1",
            title: "Investigate suspicious login",
            severity: "high",
            status: "new",
          },
        },
      },
    ] as UIMessage["parts"]

    expect(getArtifactDataPayload(part)).toEqual({
      op: "upsert",
      artifact: {
        type: "case",
        id: "case-1",
        title: "Investigate suspicious login",
        severity: "high",
        status: "new",
      },
    })
  })

  it("rejects malformed artifact data parts", () => {
    const [part] = [
      {
        type: "data-artifact",
        data: {
          op: "upsert",
          artifact: {
            type: "case",
            id: "case-1",
          },
        },
      },
    ] as UIMessage["parts"]

    expect(getArtifactDataPayload(part)).toBeUndefined()
  })

  it("builds stable artifact tab keys", () => {
    expect(artifactKey({ type: "workflow", id: "wf-1" })).toBe("workflow:wf-1")
  })
})
