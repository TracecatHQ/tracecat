/**
 * @jest-environment jsdom
 */

import { render, waitFor } from "@testing-library/react"
import WorkspacesPage from "@/app/workspaces/page"

const mockRouterReplace = jest.fn()
const mockCreateWorkspace = jest.fn()

let mockWorkspaces: { id: string; name: string }[] | undefined
let mockLastWorkspaceId: string | undefined

jest.mock("next/navigation", () => ({
  useRouter: () => ({ replace: mockRouterReplace }),
}))

jest.mock("@/components/loading/spinner", () => ({
  CenteredSpinner: () => <div>Loading</div>,
}))

jest.mock("@/lib/hooks", () => ({
  useWorkspaceManager: () => ({
    workspaces: mockWorkspaces,
    getLastWorkspaceId: () => mockLastWorkspaceId,
    createWorkspace: mockCreateWorkspace,
  }),
}))

describe("WorkspacesPage", () => {
  beforeEach(() => {
    jest.clearAllMocks()
    mockWorkspaces = undefined
    mockLastWorkspaceId = undefined
  })

  it("does not create a workspace when the visible list is empty", async () => {
    mockWorkspaces = []

    render(<WorkspacesPage />)

    await waitFor(() => {
      expect(mockCreateWorkspace).not.toHaveBeenCalled()
      expect(mockRouterReplace).not.toHaveBeenCalled()
    })
  })

  it("redirects to the last viewed existing workspace", async () => {
    mockWorkspaces = [
      { id: "workspace-1", name: "First workspace" },
      { id: "workspace-2", name: "Second workspace" },
    ]
    mockLastWorkspaceId = "workspace-2"

    render(<WorkspacesPage />)

    await waitFor(() => {
      expect(mockRouterReplace).toHaveBeenCalledWith(
        "/workspaces/workspace-2/chat"
      )
    })
    expect(mockCreateWorkspace).not.toHaveBeenCalled()
  })
})
