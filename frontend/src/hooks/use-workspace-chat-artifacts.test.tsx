import { act, renderHook, waitFor } from "@testing-library/react"
import {
  MAX_OPEN_ARTIFACT_TABS,
  useWorkspaceChatArtifacts,
} from "@/hooks/use-workspace-chat-artifacts"
import {
  parseWorkspaceChatArtifactStreamPart,
  type WorkspaceChatArtifact,
} from "@/types/workspace-chat-artifacts"

const caseArtifact: WorkspaceChatArtifact = {
  id: "case-1",
  type: "case",
  title: "Case artifact",
  severity: "medium",
  status: "new",
}

const tableArtifact: WorkspaceChatArtifact = {
  id: "table-1",
  type: "table",
  title: "Table artifact",
}

const agentArtifact: WorkspaceChatArtifact = {
  id: "agent-1",
  type: "agent",
  title: "Agent artifact",
}

describe("useWorkspaceChatArtifacts", () => {
  it("honors the initial active artifact when persisted artifacts hydrate", async () => {
    const { result, rerender } = renderHook(
      ({
        persistedArtifacts,
      }: {
        persistedArtifacts: WorkspaceChatArtifact[]
      }) =>
        useWorkspaceChatArtifacts([], {
          initialActiveArtifactKey: "table:table-1",
          persistedArtifacts,
        }),
      {
        initialProps: {
          persistedArtifacts: [] as WorkspaceChatArtifact[],
        },
      }
    )

    expect(result.current.activeArtifactKey).toBeNull()

    rerender({
      persistedArtifacts: [caseArtifact, tableArtifact],
    })

    await waitFor(() =>
      expect(result.current.activeArtifactKey).toBe("table:table-1")
    )
  })

  it("accepts streamed agent artifacts", () => {
    const part = parseWorkspaceChatArtifactStreamPart({
      type: "data-artifact",
      data: {
        op: "upsert",
        artifact: agentArtifact,
      },
    })

    expect(part).toEqual({
      type: "data-artifact",
      data: {
        op: "upsert",
        artifact: agentArtifact,
      },
    })
  })

  it("evicts the oldest artifact tabs past the open tab limit", () => {
    const { result } = renderHook(() => useWorkspaceChatArtifacts([]))

    const total = MAX_OPEN_ARTIFACT_TABS + 2
    act(() => {
      for (let i = 0; i < total; i++) {
        result.current.applyStreamPart({
          type: "data-artifact",
          data: {
            op: "upsert",
            artifact: {
              id: `case-${i}`,
              type: "case",
              title: `Case ${i}`,
              severity: "medium",
              status: "new",
            },
          },
        })
      }
    })

    expect(result.current.artifacts).toHaveLength(MAX_OPEN_ARTIFACT_TABS)
    expect(result.current.artifacts.map((artifact) => artifact.id)).toEqual(
      Array.from(
        { length: MAX_OPEN_ARTIFACT_TABS },
        (_, i) => `case-${total - MAX_OPEN_ARTIFACT_TABS + i}`
      )
    )
    expect(result.current.activeArtifactKey).toBe(`case:case-${total - 1}`)
  })

  it("defers focus changes until streaming ends", async () => {
    const { result, rerender } = renderHook(
      ({ isStreaming }: { isStreaming: boolean }) =>
        useWorkspaceChatArtifacts([], { isStreaming }),
      { initialProps: { isStreaming: true } }
    )

    const upsertCase = (i: number) =>
      result.current.applyStreamPart({
        type: "data-artifact",
        data: {
          op: "upsert",
          artifact: {
            id: `case-${i}`,
            type: "case",
            title: `Case ${i}`,
            severity: "medium",
            status: "new",
          },
        },
      })

    act(() => {
      upsertCase(0)
    })
    // First artifact of a run still gets selected so the panel isn't empty.
    await waitFor(() =>
      expect(result.current.activeArtifactKey).toBe("case:case-0")
    )

    act(() => {
      upsertCase(1)
      upsertCase(2)
    })
    // Later artifacts open in the background while streaming.
    expect(result.current.activeArtifactKey).toBe("case:case-0")

    rerender({ isStreaming: false })
    // Focus moves once to the last streamed artifact when the run ends.
    await waitFor(() =>
      expect(result.current.activeArtifactKey).toBe("case:case-2")
    )
  })

  it("caps persisted artifacts to the open tab limit", () => {
    const persistedArtifacts: WorkspaceChatArtifact[] = Array.from(
      { length: MAX_OPEN_ARTIFACT_TABS + 3 },
      (_, i) => ({
        id: `case-${i}`,
        type: "case",
        title: `Case ${i}`,
        severity: "medium",
        status: "new",
      })
    )

    const { result } = renderHook(() =>
      useWorkspaceChatArtifacts([], { persistedArtifacts })
    )

    expect(result.current.artifacts).toHaveLength(MAX_OPEN_ARTIFACT_TABS)
    expect(result.current.artifacts.at(-1)?.id).toBe(
      `case-${MAX_OPEN_ARTIFACT_TABS + 2}`
    )
  })
})
