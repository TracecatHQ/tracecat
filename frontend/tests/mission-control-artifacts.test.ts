import type { UIMessage } from "ai"
import { reduceMissionControlArtifacts } from "@/hooks/use-mission-control-artifacts"
import {
  ARTIFACT_DATA_PART_TYPE,
  artifactKey,
  parseMissionControlStreamPart,
} from "@/types/mission-control"

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

    expect(parseMissionControlStreamPart(part)).toEqual({
      type: ARTIFACT_DATA_PART_TYPE,
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

    expect(parseMissionControlStreamPart(part)).toBeUndefined()
  })

  it("reduces typed artifact stream parts by operation", () => {
    const messages = [
      {
        id: "msg-1",
        role: "assistant",
        parts: [
          {
            type: "data-artifact",
            data: {
              op: "upsert",
              artifact: {
                type: "generic",
                id: "artifact-1",
                title: "Initial result",
              },
            },
          },
          {
            type: "data-artifact",
            data: {
              op: "upsert",
              artifact: {
                type: "generic",
                id: "artifact-1",
                title: "Updated result",
              },
            },
          },
          {
            type: "data-artifact",
            data: {
              op: "upsert",
              artifact: {
                type: "generic",
                id: "artifact-2",
                title: "Second result",
              },
            },
          },
          {
            type: "data-artifact",
            data: {
              op: "remove",
              artifact: {
                type: "generic",
                id: "artifact-2",
                title: "Second result",
              },
            },
          },
        ],
      },
    ] as UIMessage[]

    expect(reduceMissionControlArtifacts(messages, new Set())).toEqual([
      {
        type: "generic",
        id: "artifact-1",
        title: "Updated result",
      },
    ])
  })

  it("builds stable artifact tab keys", () => {
    expect(artifactKey({ type: "workflow", id: "wf-1" })).toBe("workflow:wf-1")
  })
})
