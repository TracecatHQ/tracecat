import { render, screen } from "@testing-library/react"
import type { ReactNode } from "react"
import { FormsRouteLayout } from "@/components/forms/forms-route-layout"
import { useFormsAccess } from "@/hooks/use-forms-access"

const notFound = jest.fn(() => {
  throw new Error("NEXT_NOT_FOUND")
})

jest.mock("next/navigation", () => ({
  notFound: () => notFound(),
}))

jest.mock("@/hooks/use-forms-access", () => ({
  useFormsAccess: jest.fn(),
}))

jest.mock("@/components/workspaces/workspace-collection-route-layout", () => ({
  WorkspaceCollectionRouteLayout: ({ children }: { children: ReactNode }) => (
    <div data-testid="workspace-shell">{children}</div>
  ),
}))

const mockUseFormsAccess = jest.mocked(useFormsAccess)

function renderLayout() {
  return render(
    <FormsRouteLayout>
      <p>Forms page</p>
    </FormsRouteLayout>
  )
}

beforeEach(() => {
  jest.clearAllMocks()
})

describe("FormsRouteLayout", () => {
  it("answers not found while the feature flag is off", () => {
    mockUseFormsAccess.mockReturnValue("hidden")
    // React logs the error thrown by notFound().
    const consoleError = jest.spyOn(console, "error").mockImplementation()

    expect(renderLayout).toThrow("NEXT_NOT_FOUND")

    expect(notFound).toHaveBeenCalled()
    consoleError.mockRestore()
  })

  it("renders nothing of the page until access is known", () => {
    mockUseFormsAccess.mockReturnValue("loading")

    renderLayout()

    expect(notFound).not.toHaveBeenCalled()
    expect(screen.queryByText("Forms page")).not.toBeInTheDocument()
    expect(screen.queryByTestId("workspace-shell")).not.toBeInTheDocument()
  })

  it("keeps the shell and locks the page without the entitlement", () => {
    mockUseFormsAccess.mockReturnValue("locked")

    renderLayout()

    expect(notFound).not.toHaveBeenCalled()
    expect(screen.getByTestId("workspace-shell")).toBeInTheDocument()
    expect(screen.getByText("Forms aren't available")).toBeInTheDocument()
    expect(screen.getByText(/Contact your administrator/)).toBeInTheDocument()
    expect(screen.queryByText("Forms page")).not.toBeInTheDocument()
  })

  it("renders the page inside the workspace shell when enabled", () => {
    mockUseFormsAccess.mockReturnValue("enabled")

    renderLayout()

    expect(screen.getByTestId("workspace-shell")).toContainElement(
      screen.getByText("Forms page")
    )
    expect(screen.queryByText("Forms aren't available")).not.toBeInTheDocument()
  })
})
