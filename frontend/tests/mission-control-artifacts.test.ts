import { act, renderHook } from "@testing-library/react"
import type { UIMessage } from "ai"
import {
  reduceMissionControlArtifacts,
  useMissionControlArtifacts,
} from "@/hooks/use-mission-control-artifacts"
import { CHAT_SURFACE_CAPABILITIES } from "@/types/chat-surface"
import {
  ARTIFACT_DATA_PART_TYPE,
  type ArtifactDataPayload,
  artifactKey,
  type MissionControlStreamPart,
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

    expect(reduceMissionControlArtifacts(messages)).toEqual([
      {
        type: "generic",
        id: "artifact-1",
        title: "Updated result",
      },
    ])
  })

  it("lets a later artifact event re-open a locally closed artifact", () => {
    const payload: ArtifactDataPayload = {
      op: "upsert",
      artifact: {
        type: "case",
        id: "case-1",
        title: "Investigate suspicious login",
        severity: "high",
        status: "new",
      },
    }
    const streamPart: MissionControlStreamPart = {
      type: ARTIFACT_DATA_PART_TYPE,
      data: payload,
    }
    const { result } = renderHook(() => useMissionControlArtifacts([]))

    act(() => {
      result.current.applyStreamPart(streamPart)
    })

    expect(result.current.artifacts).toEqual([payload.artifact])
    expect(result.current.activeArtifactKey).toBe("case:case-1")

    act(() => {
      result.current.closeArtifact("case", "case-1")
    })

    expect(result.current.artifacts).toEqual([])
    expect(result.current.activeArtifactKey).toBeNull()

    act(() => {
      result.current.applyStreamPart(streamPart)
    })

    expect(result.current.artifacts).toEqual([payload.artifact])
    expect(result.current.activeArtifactKey).toBe("case:case-1")
  })

  it("does not replay already processed message parts after local close", () => {
    const artifact = {
      type: "case",
      id: "case-1",
      title: "Investigate suspicious login",
      severity: "high",
      status: "new",
    } satisfies ArtifactDataPayload["artifact"]
    const messages = [
      {
        id: "msg-1",
        role: "assistant",
        parts: [
          {
            type: "data-artifact",
            data: {
              op: "upsert",
              artifact,
            },
          },
        ],
      },
    ] as UIMessage[]
    const { result, rerender } = renderHook(
      ({ currentMessages }: { currentMessages: UIMessage[] }) =>
        useMissionControlArtifacts(currentMessages),
      {
        initialProps: { currentMessages: messages },
      }
    )

    expect(result.current.artifacts).toEqual([artifact])

    act(() => {
      result.current.closeArtifact("case", "case-1")
    })

    expect(result.current.artifacts).toEqual([])

    rerender({ currentMessages: [...messages] })

    expect(result.current.artifacts).toEqual([])
  })

  it("builds stable artifact tab keys", () => {
    expect(artifactKey({ type: "workflow", id: "wf-1" })).toBe("workflow:wf-1")
  })

  it("enables artifact projection only for the Mission Control surface", () => {
    expect(CHAT_SURFACE_CAPABILITIES.regular.artifacts).toBe(false)
    expect(CHAT_SURFACE_CAPABILITIES["mission-control"].artifacts).toBe(true)
  })
})
