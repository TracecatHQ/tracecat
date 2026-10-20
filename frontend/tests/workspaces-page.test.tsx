/**
 * @jest-environment jsdom
 */

import { render, waitFor } from "@testing-library/react"
import WorkspacesPage from "@/app/workspaces/page"

const mockRouterReplace = jest.fn()

let mockWorkspaceId = "workspace-1"

jest.mock("next/navigation", () => ({
  useRouter: () => ({ replace: mockRouterReplace }),
}))

jest.mock("@/components/loading/spinner", () => ({
  CenteredSpinner: () => <div>Loading</div>,
}))

// The workspace layout picks the landing workspace (last viewed, else the
// first readable one) and provides it. `layout.test.tsx` and
// `use-landing-workspace.test.tsx` cover that choice, including the empty
// list, where the layout never renders this page.
jest.mock("@/providers/workspace-id", () => ({
  useWorkspaceId: () => mockWorkspaceId,
}))

describe("WorkspacesPage", () => {
  beforeEach(() => {
    jest.clearAllMocks()
  })

  it("redirects to the workspace the layout chose", async () => {
    mockWorkspaceId = "workspace-2"

    render(<WorkspacesPage />)

    await waitFor(() => {
      expect(mockRouterReplace).toHaveBeenCalledWith("/workspaces/workspace-2")
    })
    expect(mockRouterReplace).toHaveBeenCalledTimes(1)
  })
})
