import { render, screen } from "@testing-library/react"
import type { ReactNode } from "react"
import { FormsRouteLayout } from "@/components/forms/forms-route-layout"
import { useFormsAccess } from "@/hooks/use-forms-access"
import { useChatPageContext } from "@/providers/chat-page-context"

const notFound = jest.fn(() => {
  throw new Error("NEXT_NOT_FOUND")
})

const mockParams: { formId?: string } = {}

jest.mock("next/navigation", () => ({
  notFound: () => notFound(),
  useParams: () => mockParams,
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

function PageContextProbe() {
  const pageContext = useChatPageContext()
  return <output>{JSON.stringify(pageContext)}</output>
}

function renderLayout() {
  return render(
    <FormsRouteLayout>
      <p>Forms page</p>
      <PageContextProbe />
    </FormsRouteLayout>
  )
}

beforeEach(() => {
  jest.clearAllMocks()
  mockParams.formId = undefined
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

  it("gives the docked chat the open form as page context", () => {
    mockUseFormsAccess.mockReturnValue("enabled")
    mockParams.formId = "form-1"

    renderLayout()

    expect(screen.getByRole("status")).toHaveTextContent(
      '{"kind":"form","id":"form-1"}'
    )
  })

  it("gives no page context on the forms list", () => {
    mockUseFormsAccess.mockReturnValue("enabled")

    renderLayout()

    expect(screen.getByRole("status")).toHaveTextContent("null")
  })
})

describe("useChatPageContext", () => {
  it("is empty outside a provider, so other pages send nothing", () => {
    render(<PageContextProbe />)

    expect(screen.getByRole("status")).toHaveTextContent("null")
  })
})
