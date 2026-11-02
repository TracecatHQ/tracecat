/**
 * @jest-environment jsdom
 */

import { render, screen, waitFor } from "@testing-library/react"
import WorkspacePage from "@/app/workspaces/[workspaceId]/page"
import { getWorkspaceLandingPath } from "@/lib/workspace-navigation"

const mockRouterReplace = jest.fn()
const mockUseScopeCheck = jest.fn<boolean | undefined, [string]>()
const mockHasEntitlement = jest.fn<boolean, [string]>()
let mockScopes: Record<string, boolean | undefined> = {}
let mockEntitlements: Record<string, boolean> = {}
let mockEntitlementsLoading = false

jest.mock("next/navigation", () => ({
  useRouter: () => ({ replace: mockRouterReplace }),
}))

jest.mock("next/image", () => ({
  __esModule: true,
  default: (props: {
    src: string
    alt: string
    className?: string
  }): JSX.Element => <img alt={props.alt} className={props.className} />,
}))

jest.mock("@/components/auth/scope-guard", () => ({
  useScopeCheck: (scope: string) => mockUseScopeCheck(scope),
}))

jest.mock("@/components/loading/spinner", () => ({
  CenteredSpinner: () => <div>Loading</div>,
}))

jest.mock("@/hooks", () => ({
  useEntitlements: () => ({
    hasEntitlement: mockHasEntitlement,
    isLoading: mockEntitlementsLoading,
  }),
}))

jest.mock("@/providers/workspace-id", () => ({
  useWorkspaceId: () => "workspace-1",
}))

describe("WorkspacePage", () => {
  beforeEach(() => {
    mockRouterReplace.mockReset()
    mockUseScopeCheck.mockReset()
    mockHasEntitlement.mockReset()
    mockScopes = {}
    mockEntitlements = {}
    mockEntitlementsLoading = false
    mockUseScopeCheck.mockImplementation((scope) => mockScopes[scope] ?? false)
    mockHasEntitlement.mockImplementation(
      (entitlement) => mockEntitlements[entitlement] ?? false
    )
  })

  it("does not redirect service-account-only users into the workspace shell", () => {
    mockScopes = {
      "workspace:service_account:read": true,
      "workspace:read": false,
    }
    mockHasEntitlement.mockReturnValue(true)

    render(<WorkspacePage />)

    expect(mockRouterReplace).not.toHaveBeenCalled()
    expect(screen.getByText("No accessible pages")).toBeInTheDocument()
  })

  it("redirects workspace-readable service-account users to service accounts", async () => {
    mockScopes = {
      "workspace:service_account:read": true,
      "workspace:read": true,
    }
    mockHasEntitlement.mockImplementation(
      (entitlement) => entitlement === "service_accounts"
    )

    render(<WorkspacePage />)

    await waitFor(() => {
      expect(mockRouterReplace).toHaveBeenCalledWith(
        "/workspaces/workspace-1/service-accounts"
      )
    })
  })

  it("redirects to Chat when the workspace shell is readable", async () => {
    expect(getWorkspaceLandingPath("workspace-1")).toBe(
      "/workspaces/workspace-1"
    )
    mockScopes = {
      "agent:execute": true,
      "agent:read": true,
      "workspace:read": true,
    }
    mockEntitlements = {
      workspace_chat: true,
    }

    render(<WorkspacePage />)

    await waitFor(() => {
      expect(mockRouterReplace).toHaveBeenCalledWith(
        "/workspaces/workspace-1/chat"
      )
    })
  })

  it("falls back to workflows when Chat is not entitled", async () => {
    mockScopes = {
      "workspace:read": true,
      "workflow:read": true,
      "agent:read": true,
      "agent:execute": true,
    }
    mockEntitlements = {
      workspace_chat: false,
    }

    render(<WorkspacePage />)

    await waitFor(() => {
      expect(mockRouterReplace).toHaveBeenCalledWith(
        "/workspaces/workspace-1/workflows"
      )
    })
  })

  it("falls back to agents when Chat, workflows, and cases are unavailable", async () => {
    mockScopes = {
      "workspace:read": true,
      "agent:read": true,
    }
    mockEntitlements = {
      workspace_chat: true,
    }

    render(<WorkspacePage />)

    await waitFor(() => {
      expect(mockRouterReplace).toHaveBeenCalledWith(
        "/workspaces/workspace-1/agents"
      )
    })
  })

  it("waits for entitlements before selecting a landing page", () => {
    mockEntitlementsLoading = true
    mockScopes = {
      "workflow:read": true,
    }

    render(<WorkspacePage />)

    expect(mockRouterReplace).not.toHaveBeenCalled()
    expect(screen.getByText("Loading")).toBeInTheDocument()
  })
})
