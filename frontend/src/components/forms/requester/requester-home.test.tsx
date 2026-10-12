import { render, screen, within } from "@testing-library/react"
import type { ReactNode } from "react"
import { formsListIntakeForms, formsListIntakeSubmissions } from "@/client"
import { RequesterHome } from "@/components/forms/requester/requester-home"
import { QueryClient, QueryClientProvider } from "@/lib/query"

let searchParams = new URLSearchParams()

jest.mock("next/navigation", () => ({
  useSearchParams: () => searchParams,
  useRouter: () => ({ replace: jest.fn() }),
  notFound: jest.fn(),
}))

jest.mock("@/client", () => ({
  formsListIntakeForms: jest.fn(),
  formsListIntakeSubmissions: jest.fn(),
  formsLocateIntakeForm: jest.fn(),
}))

jest.mock("@/hooks/use-auth", () => ({
  useAuth: () => ({
    user: {
      email: "maya.okafor@example.com",
      firstName: "Maya",
      getDisplayName: () => "Maya Okafor",
    },
    userIsLoading: false,
  }),
  useAuthActions: () => ({ logout: jest.fn() }),
}))

jest.mock("@/hooks/use-forms-access", () => ({
  useFormsAccess: () => "enabled",
}))

const listForms = jest.mocked(formsListIntakeForms)
const listSubmissions = jest.mocked(formsListIntakeSubmissions)

function renderHome(query = "") {
  searchParams = new URLSearchParams(query)
  const queryClient = new QueryClient({
    defaultOptions: { queries: { retry: false } },
  })
  render(<RequesterHome />, {
    wrapper: ({ children }: { children: ReactNode }) => (
      <QueryClientProvider client={queryClient}>{children}</QueryClientProvider>
    ),
  })
}

beforeEach(() => {
  jest.clearAllMocks()
  listForms.mockResolvedValue({
    items: [
      {
        id: "form-1",
        workspace_id: "workspace-1",
        workspace_name: "Acme Security",
        title: "Laptop request",
        description: "Request a **new laptop** from [IT](https://example.com).",
      },
      {
        id: "form-2",
        workspace_id: "workspace-2",
        workspace_name: "People Operations",
        title: "New hire equipment",
      },
      {
        id: "form-3",
        workspace_id: "workspace-1",
        workspace_name: "Acme Security",
        title: "Report a phishing email",
      },
    ],
    has_more: false,
  })
  listSubmissions.mockResolvedValue({
    items: [
      {
        id: "submission-1",
        reference: "FRM-8F3K2A1C",
        form_id: "form-1",
        form_title: "Laptop request",
        workspace_id: "workspace-1",
        workspace_name: "Acme Security",
        submitted_at: "2026-10-12T10:42:00Z",
      },
    ],
    has_more: false,
  })
})

describe("RequesterHome", () => {
  it("lists forms grouped by workspace, each linking to its fill-in page", async () => {
    renderHome()

    const acme = (
      await screen.findByRole("heading", { name: "Acme Security" })
    ).closest("section") as HTMLElement
    expect(
      within(acme)
        .getAllByRole("link")
        .map((link) => link.getAttribute("href"))
    ).toEqual(["/forms/form-1", "/forms/form-3"])
    expect(
      within(acme).getByText("Request a new laptop from IT.")
    ).toBeInTheDocument()

    const people = screen
      .getByRole("heading", { name: "People Operations" })
      .closest("section") as HTMLElement
    expect(within(people).getByRole("link")).toHaveAttribute(
      "href",
      "/forms/form-2"
    )
    expect(screen.getByRole("link", { name: "Forms" })).toHaveAttribute(
      "aria-current",
      "page"
    )
    expect(listSubmissions).not.toHaveBeenCalled()
  })

  it("shows an empty state when there are no forms", async () => {
    listForms.mockResolvedValue({ items: [], has_more: false })

    renderHome()

    expect(await screen.findByText("No forms yet")).toBeInTheDocument()
  })

  it("lists the requester's submissions without any run status", async () => {
    renderHome("tab=submissions")

    expect(await screen.findByText("FRM-8F3K2A1C")).toBeInTheDocument()
    expect(
      screen.getAllByRole("columnheader").map((cell) => cell.textContent)
    ).toEqual(["Form", "Reference", "Submitted"])
    expect(
      screen.getByRole("link", { name: "My submissions" })
    ).toHaveAttribute("aria-current", "page")
    expect(listForms).not.toHaveBeenCalled()
  })
})
