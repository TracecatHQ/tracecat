import { renderHook, waitFor } from "@testing-library/react"
import { useWorkspaceChatArtifacts } from "@/hooks/use-workspace-chat-artifacts"
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
})
