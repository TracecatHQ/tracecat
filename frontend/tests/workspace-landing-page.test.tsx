/**
 * @jest-environment jsdom
 */

import { render, screen, waitFor } from "@testing-library/react"
import WorkspacePage from "@/app/workspaces/[workspaceId]/page"

const mockRouterReplace = jest.fn()
const mockUseScopeCheck = jest.fn<boolean | undefined, [string]>()
let mockScopes: Record<string, boolean | undefined> = {}

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
    hasEntitlement: () => false,
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

  it("redirects to service accounts when the workspace shell is readable", async () => {
    mockScopes = {
      "workspace:service_account:read": true,
      "workspace:read": true,
    }

    render(<WorkspacePage />)

    await waitFor(() => {
      expect(mockRouterReplace).toHaveBeenCalledWith(
        "/workspaces/workspace-1/service-accounts"
      )
    })
  })
})
