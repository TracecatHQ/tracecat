import { QueryClient } from "@tanstack/react-query"
import { act, renderHook } from "@testing-library/react"
import type { UIMessage } from "ai"
import { invalidateArtifactQueries } from "@/components/workspace-chat/artifacts/artifact-registry"
import {
  reduceWorkspaceChatArtifacts,
  useWorkspaceChatArtifacts,
} from "@/hooks/use-workspace-chat-artifacts"
import { CHAT_SURFACE_CAPABILITIES } from "@/types/chat-surface"
import {
  ARTIFACT_DATA_PART_TYPE,
  type ArtifactDataPayload,
  artifactKey,
  parseWorkspaceChatArtifactStreamPart,
  type WorkspaceChatArtifactStreamPart,
} from "@/types/workspace-chat-artifacts"

describe("workspace chat artifacts", () => {
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

    expect(parseWorkspaceChatArtifactStreamPart(part)).toEqual({
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

    expect(parseWorkspaceChatArtifactStreamPart(part)).toBeUndefined()
  })

  it("rejects incomplete artifact subtype payloads", () => {
    const [part] = [
      {
        type: "data-artifact",
        data: {
          op: "upsert",
          artifact: {
            type: "run",
            id: "run-1",
            title: "Workflow run",
            status: "success",
            startedAt: "2026-05-29T12:00:00.000Z",
          },
        },
      },
    ] as UIMessage["parts"]

    expect(parseWorkspaceChatArtifactStreamPart(part)).toBeUndefined()
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

    expect(reduceWorkspaceChatArtifacts(messages)).toEqual([
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
    const streamPart: WorkspaceChatArtifactStreamPart = {
      type: ARTIFACT_DATA_PART_TYPE,
      data: payload,
    }
    const { result } = renderHook(() => useWorkspaceChatArtifacts([]))

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

  it("notifies when explicit artifact stream parts are applied", () => {
    const streamPart: WorkspaceChatArtifactStreamPart = {
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
    }
    const onArtifactStreamPart = jest.fn()
    const { result } = renderHook(() =>
      useWorkspaceChatArtifacts([], { onArtifactStreamPart })
    )

    act(() => {
      result.current.applyStreamPart(streamPart)
    })

    expect(onArtifactStreamPart).toHaveBeenCalledWith(streamPart)
  })

  it("notifies when new artifact message parts arrive", () => {
    const streamPart: WorkspaceChatArtifactStreamPart = {
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
    }
    const messages = [
      {
        id: "msg-1",
        role: "assistant",
        parts: [streamPart],
      },
    ] as UIMessage[]
    const onArtifactStreamPart = jest.fn()
    const { rerender } = renderHook(
      ({ currentMessages }: { currentMessages: UIMessage[] }) =>
        useWorkspaceChatArtifacts(currentMessages, {
          onArtifactStreamPart,
        }),
      {
        initialProps: { currentMessages: [] as UIMessage[] },
      }
    )

    rerender({ currentMessages: messages })

    expect(onArtifactStreamPart).toHaveBeenCalledWith(streamPart)
  })

  it("hydrates artifacts from the persisted session projection", () => {
    const artifact = {
      type: "case",
      id: "case-1",
      title: "Investigate suspicious login",
      severity: "high",
      status: "new",
    } satisfies ArtifactDataPayload["artifact"]

    const { result } = renderHook(() =>
      useWorkspaceChatArtifacts([], { persistedArtifacts: [artifact] })
    )

    expect(result.current.artifacts).toEqual([artifact])
    expect(result.current.activeArtifactKey).toBe("case:case-1")
  })

  it("rehydrates persisted artifacts after re-enabling", () => {
    const artifact = {
      type: "case",
      id: "case-1",
      title: "Investigate suspicious login",
      severity: "high",
      status: "new",
    } satisfies ArtifactDataPayload["artifact"]

    const { result, rerender } = renderHook(
      ({ enabled }: { enabled: boolean }) =>
        useWorkspaceChatArtifacts([], {
          enabled,
          persistedArtifacts: [artifact],
        }),
      {
        initialProps: { enabled: true },
      }
    )

    expect(result.current.artifacts).toEqual([artifact])

    rerender({ enabled: false })

    expect(result.current.artifacts).toEqual([])

    rerender({ enabled: true })

    expect(result.current.artifacts).toEqual([artifact])
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
        useWorkspaceChatArtifacts(currentMessages),
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

  it("keeps a closed artifact removed when the persisted projection updates", () => {
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
    const onCloseArtifact = jest.fn()
    const { result, rerender } = renderHook(
      ({
        persistedArtifacts,
      }: {
        persistedArtifacts: ArtifactDataPayload["artifact"][]
      }) =>
        useWorkspaceChatArtifacts(messages, {
          persistedArtifacts,
          onCloseArtifact,
        }),
      {
        initialProps: { persistedArtifacts: [artifact] },
      }
    )

    expect(result.current.artifacts).toEqual([artifact])

    act(() => {
      result.current.closeArtifact("case", "case-1")
    })

    expect(onCloseArtifact).toHaveBeenCalledWith("case", "case-1")
    expect(result.current.artifacts).toEqual([])

    rerender({ persistedArtifacts: [] })

    expect(result.current.artifacts).toEqual([])
  })

  it("builds stable artifact tab keys", () => {
    expect(artifactKey({ type: "workflow", id: "wf-1" })).toBe("workflow:wf-1")
  })

  it("enables artifact projection only for the workspace chat surface", () => {
    expect(CHAT_SURFACE_CAPABILITIES.regular.artifacts).toBe(false)
    expect(CHAT_SURFACE_CAPABILITIES["workspace-chat"].artifacts).toBe(true)
  })

  it("invalidates case artifact queries for embedded artifact refreshes", () => {
    const queryClient = new QueryClient()
    const invalidateQueries = jest.spyOn(queryClient, "invalidateQueries")

    invalidateArtifactQueries(queryClient, "workspace-1", {
      type: "case",
      id: "case-1",
      title: "Investigate suspicious login",
      severity: "high",
      status: "new",
    })

    expect(invalidateQueries).toHaveBeenCalledWith({
      queryKey: ["case", "case-1"],
    })
    expect(invalidateQueries).toHaveBeenCalledWith({
      queryKey: ["cases", "workspace-1"],
    })
    expect(invalidateQueries).toHaveBeenCalledWith({
      queryKey: ["cases", "paginated"],
      exact: false,
    })
  })
})
