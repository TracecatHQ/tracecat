/**
 * @jest-environment jsdom
 */

import { QueryClient, QueryClientProvider } from "@tanstack/react-query"
import { render, screen } from "@testing-library/react"
import { WorkspaceChatView } from "@/components/workspace-chat/workspace-chat-view"

const mockRouterReplace = jest.fn()
const mockUseScopeCheck = jest.fn<
  boolean | undefined,
  [string | undefined, string[] | undefined, { all?: boolean } | undefined]
>()
const mockHasEntitlement = jest.fn<boolean, [string]>()

jest.mock("next/navigation", () => ({
  useRouter: () => ({ replace: mockRouterReplace }),
  useSearchParams: () => new URLSearchParams(),
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

jest.mock("@/components/entitlement-required-empty-state", () => ({
  EntitlementRequiredEmptyState: () => <div>Upgrade required</div>,
}))

jest.mock("@/components/loading/spinner", () => ({
  CenteredSpinner: () => <div>Loading</div>,
}))

jest.mock("@/components/workspace-chat/artifacts/artifact-panel", () => ({
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

jest.mock("@/hooks/use-workspace-chat-artifacts", () => ({
  useWorkspaceChatArtifacts: () => ({
    artifacts: [],
    activeArtifactKey: null,
    setActiveArtifactKey: jest.fn(),
    closeArtifact: jest.fn(),
  }),
}))

jest.mock("@/providers/workspace-id", () => ({
  useWorkspaceId: () => "workspace-1",
}))

function renderWorkspaceChatView() {
  const queryClient = new QueryClient()
  return render(
    <QueryClientProvider client={queryClient}>
      <WorkspaceChatView />
    </QueryClientProvider>
  )
}

describe("WorkspaceChatView", () => {
  beforeEach(() => {
    mockRouterReplace.mockReset()
    mockUseScopeCheck.mockReset()
    mockHasEntitlement.mockReset()
    mockUseScopeCheck.mockReturnValue(true)
    mockHasEntitlement.mockReturnValue(true)
  })

  it("requires agent execute and read scopes before rendering chat", () => {
    renderWorkspaceChatView()

    expect(mockUseScopeCheck).toHaveBeenCalledWith(
      undefined,
      ["agent:execute", "agent:read"],
      { all: true }
    )
    expect(screen.getByTestId("mission-control-chat")).toBeInTheDocument()
  })

  it("does not render chat without all required agent scopes", () => {
    mockUseScopeCheck.mockReturnValue(false)

    renderWorkspaceChatView()

    expect(mockRouterReplace).toHaveBeenCalledWith("/workspaces/workspace-1")
    expect(screen.queryByTestId("mission-control-chat")).not.toBeInTheDocument()
  })

  it("shows upgrade state when workspace chat is unavailable", () => {
    mockHasEntitlement.mockReturnValue(false)

    renderWorkspaceChatView()

    expect(screen.getByText("Upgrade required")).toBeInTheDocument()
    expect(screen.queryByTestId("mission-control-chat")).not.toBeInTheDocument()
  })
})
