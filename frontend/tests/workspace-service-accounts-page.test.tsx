/**
 * @jest-environment jsdom
 */

import { render, screen } from "@testing-library/react"
import WorkspaceServiceAccountsPage from "@/app/workspaces/[workspaceId]/service-accounts/page"

const mockNotFound = jest.fn()
const mockUseScopeCheck = jest.fn<boolean | undefined, [string]>()

jest.mock("next/navigation", () => ({
  notFound: () => {
    mockNotFound()
    throw new Error("NEXT_NOT_FOUND")
  },
}))

jest.mock("@/components/auth/scope-guard", () => ({
  useScopeCheck: (scope: string) => mockUseScopeCheck(scope),
}))

jest.mock("@/components/organization/workspace-service-accounts", () => ({
  WorkspaceServiceAccounts: () => <div>Workspace service accounts</div>,
}))

describe("WorkspaceServiceAccountsPage", () => {
  let consoleErrorSpy: jest.SpyInstance

  beforeEach(() => {
    mockNotFound.mockReset()
    mockUseScopeCheck.mockReset()
    consoleErrorSpy = jest.spyOn(console, "error").mockImplementation(() => {})
  })

  afterEach(() => {
    consoleErrorSpy.mockRestore()
  })

  it("waits for scope resolution before rendering the page", () => {
    mockUseScopeCheck.mockReturnValue(undefined)

    const { container } = render(<WorkspaceServiceAccountsPage />)

    expect(mockUseScopeCheck).toHaveBeenCalledWith(
      "workspace:service_account:read"
    )
    expect(mockNotFound).not.toHaveBeenCalled()
    expect(
      screen.queryByText("Workspace service accounts")
    ).not.toBeInTheDocument()
    expect(container).toBeEmptyDOMElement()
  })

  it("navigates to not found when the scope check fails", () => {
    mockUseScopeCheck.mockReturnValue(false)

    expect(() => render(<WorkspaceServiceAccountsPage />)).toThrow(
      "NEXT_NOT_FOUND"
    )

    expect(mockNotFound).toHaveBeenCalled()
    expect(
      screen.queryByText("Workspace service accounts")
    ).not.toBeInTheDocument()
  })

  it("renders the page when the scope check succeeds", () => {
    mockUseScopeCheck.mockReturnValue(true)

    render(<WorkspaceServiceAccountsPage />)

    expect(mockNotFound).not.toHaveBeenCalled()
    expect(screen.getByText("Workspace service accounts")).toBeInTheDocument()
  })
})
