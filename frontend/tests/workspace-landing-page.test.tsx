/**
 * @jest-environment jsdom
 */

import { render, screen, waitFor } from "@testing-library/react"
import WorkspacePage from "@/app/workspaces/[workspaceId]/page"

const mockRouterReplace = jest.fn()
const mockUseScopeCheck = jest.fn<boolean | undefined, [string]>()
let mockScopes: Record<string, boolean | undefined> = {}
let mockEntitlements: Record<string, boolean> = {}

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
    hasEntitlement: (entitlement: string) =>
      mockEntitlements[entitlement] ?? false,
    isLoading: false,
  }),
}))

jest.mock("@/providers/workspace-id", () => ({
  useWorkspaceId: () => "workspace-1",
}))

describe("WorkspacePage", () => {
  beforeEach(() => {
    mockRouterReplace.mockReset()
    mockUseScopeCheck.mockReset()
    mockScopes = {}
    mockEntitlements = {}
    mockUseScopeCheck.mockImplementation((scope) => mockScopes[scope] ?? false)
  })

  it("does not redirect service-account-only users into the workspace shell", () => {
    mockScopes = {
      "workspace:service_account:read": true,
      "workspace:read": false,
    }

    render(<WorkspacePage />)

    expect(mockRouterReplace).not.toHaveBeenCalled()
    expect(screen.getByText("No accessible pages")).toBeInTheDocument()
  })

  it("redirects to Chat when the workspace shell is readable", async () => {
    mockScopes = {
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

  it("falls back when Chat is not entitled", async () => {
    mockScopes = {
      "workspace:read": true,
      "workflow:read": true,
    }

    render(<WorkspacePage />)

    await waitFor(() => {
      expect(mockRouterReplace).toHaveBeenCalledWith(
        "/workspaces/workspace-1/workflows"
      )
    })
  })
})
