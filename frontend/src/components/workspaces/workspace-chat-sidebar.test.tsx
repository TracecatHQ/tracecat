import { render } from "@testing-library/react"
import type { ReactNode } from "react"
import { WorkspaceChatSidebar } from "@/components/workspaces/workspace-chat-sidebar"

const mockHandleStreamData = jest.fn()
const mockUseArtifactInvalidation = jest.fn()
const mockChatInterface = jest.fn((_props: Record<string, unknown>) => null)

jest.mock("@/components/chat/chat-interface", () => ({
  ChatInterface: (props: Record<string, unknown>) => mockChatInterface(props),
}))

jest.mock("@/components/ui/resizable-sidebar", () => ({
  ResizableSidebar: ({ children }: { children: ReactNode }) => (
    <div>{children}</div>
  ),
}))

jest.mock("@/hooks/use-artifact-invalidation", () => ({
  useArtifactInvalidation: (workspaceId: string) => {
    mockUseArtifactInvalidation(workspaceId)
    return {
      handleStreamData: mockHandleStreamData,
      handleArtifactStreamPart: jest.fn(),
    }
  },
}))

jest.mock("@/providers/workspace-id", () => ({
  useWorkspaceId: () => "workspace-1",
}))

describe("WorkspaceChatSidebar", () => {
  it("passes the artifact handler to the docked chat as onData", () => {
    render(<WorkspaceChatSidebar />)

    expect(mockUseArtifactInvalidation).toHaveBeenCalledWith("workspace-1")
    expect(mockChatInterface).toHaveBeenCalledWith({
      entityType: "copilot",
      entityId: "workspace-1",
      onData: mockHandleStreamData,
    })
  })
})
