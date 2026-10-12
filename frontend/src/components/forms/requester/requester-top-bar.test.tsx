import { render, screen, waitFor } from "@testing-library/react"
import userEvent from "@testing-library/user-event"
import type { ReactNode } from "react"
import { usersGetMyScopes, workspacesListWorkspaces } from "@/client"
import { RequesterTopBar } from "@/components/forms/requester/requester-top-bar"
import { QueryClient, QueryClientProvider } from "@/lib/query"

jest.mock("@/client", () => ({
  usersGetMyScopes: jest.fn(),
  workspacesListWorkspaces: jest.fn(),
}))

jest.mock("@/hooks/use-auth", () => ({
  useAuth: () => ({
    user: {
      email: "maya.okafor@example.com",
      firstName: "Maya",
      getDisplayName: () => "Maya Okafor",
    },
  }),
  useAuthActions: () => ({ logout: jest.fn() }),
}))

const listWorkspaces = jest.mocked(workspacesListWorkspaces)
const getMyScopes = jest.mocked(usersGetMyScopes)

const SCOPES_BY_WORKSPACE: Record<string, string[]> = {
  "workspace-intake": ["form:read", "form:submit"],
  "workspace-builder": ["workspace:read", "workflow:read", "form:read"],
  "workspace-admin": ["*"],
}

function renderTopBar() {
  const queryClient = new QueryClient({
    defaultOptions: { queries: { retry: false } },
  })
  function wrapper({ children }: { children: ReactNode }) {
    return (
      <QueryClientProvider client={queryClient}>{children}</QueryClientProvider>
    )
  }
  return render(<RequesterTopBar title="Forms" />, { wrapper })
}

beforeEach(() => {
  jest.clearAllMocks()
  listWorkspaces.mockResolvedValue([
    { id: "workspace-intake", name: "Helpdesk" },
    { id: "workspace-builder", name: "Security" },
    { id: "workspace-admin", name: "Platform" },
  ] as never)
  getMyScopes.mockImplementation(
    ({ workspaceId } = {}) =>
      Promise.resolve({
        scopes: SCOPES_BY_WORKSPACE[workspaceId ?? ""] ?? [],
      }) as never
  )
})

describe("RequesterTopBar account menu", () => {
  it("asks nothing about workspaces until the menu opens", () => {
    renderTopBar()

    expect(listWorkspaces).not.toHaveBeenCalled()
    expect(getMyScopes).not.toHaveBeenCalled()
  })

  it("offers the workspaces the user can open, and only those", async () => {
    renderTopBar()

    await userEvent.click(
      screen.getByRole("button", { name: "Account menu for Maya Okafor" })
    )

    expect(
      await screen.findByRole("menuitem", { name: "Security" })
    ).toHaveAttribute("href", "/workspaces/workspace-builder")
    expect(screen.getByRole("menuitem", { name: "Platform" })).toHaveAttribute(
      "href",
      "/workspaces/workspace-admin"
    )
    expect(screen.getByText("Open workspace")).toBeInTheDocument()
    // Intake-only here: opening it would only bounce back to /forms.
    expect(
      screen.queryByRole("menuitem", { name: "Helpdesk" })
    ).not.toBeInTheDocument()
    expect(screen.getByRole("menuitem", { name: "Sign out" })).toBeVisible()
  })

  it("shows no workspace section to a user who is intake-only everywhere", async () => {
    listWorkspaces.mockResolvedValue([
      { id: "workspace-intake", name: "Helpdesk" },
    ] as never)

    renderTopBar()
    await userEvent.click(
      screen.getByRole("button", { name: "Account menu for Maya Okafor" })
    )

    expect(
      await screen.findByRole("menuitem", { name: "Sign out" })
    ).toBeVisible()
    await waitFor(() =>
      expect(getMyScopes).toHaveBeenCalledWith({
        workspaceId: "workspace-intake",
      })
    )
    expect(screen.queryByText("Open workspace")).not.toBeInTheDocument()
  })
})
