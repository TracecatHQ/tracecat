import { render, screen } from "@testing-library/react"
import { type ReactNode, useContext } from "react"
import { FormsRouteLayout } from "@/components/forms/forms-route-layout"
import type { FormsAccess } from "@/hooks/use-forms-access"
import { PageContext } from "@/providers/chat-page-context"

let access: FormsAccess = "enabled"
let params: { formId?: string } = {}

jest.mock("next/navigation", () => ({
  notFound: () => {
    throw new Error("NEXT_NOT_FOUND")
  },
  useParams: () => params,
}))
jest.mock("@/hooks/use-forms-access", () => ({
  useFormsAccess: () => access,
}))
jest.mock("@/components/workspaces/workspace-collection-route-layout", () => ({
  WorkspaceCollectionRouteLayout: ({ children }: { children: ReactNode }) => (
    <div data-testid="workspace-shell">{children}</div>
  ),
}))

function Page() {
  return <output>{JSON.stringify(useContext(PageContext))}</output>
}

const renderLayout = () =>
  render(
    <FormsRouteLayout>
      <Page />
    </FormsRouteLayout>
  )

describe("FormsRouteLayout", () => {
  it("is a 404 with the flag off and renders nothing while access loads", () => {
    access = "hidden"
    jest.spyOn(console, "error").mockImplementation(() => {})
    expect(renderLayout).toThrow("NEXT_NOT_FOUND")
    jest.restoreAllMocks()

    access = "loading"
    renderLayout()
    expect(screen.queryByTestId("workspace-shell")).not.toBeInTheDocument()
  })

  it("keeps the shell and locks the page without the entitlement", () => {
    access = "locked"

    renderLayout()

    expect(screen.getByTestId("workspace-shell")).toBeInTheDocument()
    expect(screen.getByText("Forms aren't available")).toBeInTheDocument()
    expect(screen.queryByRole("status")).not.toBeInTheDocument()
  })

  it("gives the docked chat the open form as page context", () => {
    access = "enabled"
    params = { formId: "form-1" }

    renderLayout()

    expect(screen.getByRole("status")).toHaveTextContent(
      '{"kind":"form","id":"form-1"}'
    )
  })
})
