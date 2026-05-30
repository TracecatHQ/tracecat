/**
 * @jest-environment jsdom
 */

import { render, screen, waitFor } from "@testing-library/react"
import { MissionControlView } from "@/components/mission-control/mission-control-view"

const mockRouterReplace = jest.fn()
const mockUseScopeCheck = jest.fn<
  boolean | undefined,
  [string | undefined, string[] | undefined, { all?: boolean } | undefined]
>()
const mockHasEntitlement = jest.fn<boolean, [string]>()

jest.mock("next/navigation", () => ({
  useRouter: () => ({ replace: mockRouterReplace }),
}))

jest.mock("@/components/auth/scope-guard", () => ({
  useScopeCheck: (
    scope?: string,
    scopes?: string[],
    options?: { all?: boolean }
  ) => mockUseScopeCheck(scope, scopes, options),
}))

jest.mock("@/components/chat/chat-interface", () => ({
  ChatInterface: () => <div data-testid="mission-control-chat" />,
}))

jest.mock("@/components/loading/spinner", () => ({
  CenteredSpinner: () => <div>Loading</div>,
}))

jest.mock("@/components/mission-control/artifact-panel", () => ({
  ArtifactPanel: () => <div data-testid="artifact-panel" />,
}))

jest.mock("@/hooks/use-chat", () => ({
  useRemoveSessionArtifact: () => ({ removeArtifact: jest.fn() }),
}))

jest.mock("@/hooks/use-entitlements", () => ({
  useEntitlements: () => ({
    hasEntitlement: mockHasEntitlement,
    isLoading: false,
  }),
}))

jest.mock("@/hooks/use-mission-control-artifacts", () => ({
  useMissionControlArtifacts: () => ({
    artifacts: [],
    activeArtifactKey: null,
    setActiveArtifactKey: jest.fn(),
    closeArtifact: jest.fn(),
  }),
}))

jest.mock("@/providers/workspace-id", () => ({
  useWorkspaceId: () => "workspace-1",
}))

describe("MissionControlView", () => {
  beforeEach(() => {
    mockRouterReplace.mockReset()
    mockUseScopeCheck.mockReset()
    mockHasEntitlement.mockReset()
    mockUseScopeCheck.mockReturnValue(true)
    mockHasEntitlement.mockReturnValue(true)
  })

  it("requires agent execute and read scopes before rendering chat", () => {
    render(<MissionControlView />)

    expect(mockUseScopeCheck).toHaveBeenCalledWith(
      undefined,
      ["agent:execute", "agent:read"],
      { all: true }
    )
    expect(screen.getByTestId("mission-control-chat")).toBeInTheDocument()
  })

  it("does not render chat without all required agent scopes", () => {
    mockUseScopeCheck.mockReturnValue(false)

    render(<MissionControlView />)

    expect(screen.queryByTestId("mission-control-chat")).not.toBeInTheDocument()
  })

  it("redirects to workspaces when agent add-ons are unavailable", async () => {
    mockHasEntitlement.mockReturnValue(false)

    render(<MissionControlView />)

    await waitFor(() => {
      expect(mockRouterReplace).toHaveBeenCalledWith("/workspaces")
    })
    expect(screen.queryByTestId("mission-control-chat")).not.toBeInTheDocument()
  })
})
