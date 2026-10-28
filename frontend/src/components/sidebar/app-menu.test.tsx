import { render, screen } from "@testing-library/react"
import userEvent from "@testing-library/user-event"
import { AppMenu } from "@/components/sidebar/app-menu"
import { SidebarProvider } from "@/components/ui/sidebar"

const mockPush = jest.fn()
let mockWorkspaces: { id: string; name: string }[] = []

jest.mock("next/navigation", () => ({
  useRouter: () => ({ push: mockPush }),
}))
jest.mock("@/hooks/use-mobile", () => ({
  useIsMobile: () => false,
}))
jest.mock("@/lib/hooks", () => ({
  useWorkspaceManager: () => ({
    workspaces: mockWorkspaces,
    createWorkspace: jest.fn(),
  }),
}))
jest.mock("@/hooks/use-organization", () => ({
  useOrganization: () => ({ organization: { id: "org-1", name: "Org" } }),
  useOrganizationMemberships: () => ({ organizations: [] }),
}))
jest.mock("@/components/auth/scope-guard", () => ({
  useScopeCheck: () => false,
}))
jest.mock("@/hooks/use-entitlements", () => ({
  useEntitlements: () => ({
    hasEntitlement: () => false,
    hasEntitlementData: true,
    isLoading: false,
  }),
}))

function makeWorkspaces(names: string[]) {
  return names.map((name, i) => ({ id: `ws-${i}`, name }))
}

async function openMenu() {
  render(
    <SidebarProvider>
      <AppMenu workspaceId="ws-0" />
    </SidebarProvider>
  )
  await userEvent.click(screen.getByRole("button", { name: /alpha/i }))
}

function workspaceItemNames() {
  return screen
    .getAllByRole("menuitem")
    .map((item) => item.textContent ?? "")
    .filter((text) => !text.includes("Create workspace"))
}

beforeEach(() => {
  mockPush.mockReset()
})

it("hides the search input with five or fewer workspaces", async () => {
  mockWorkspaces = makeWorkspaces([
    "Alpha",
    "Bravo",
    "Charlie",
    "Delta",
    "Echo",
  ])
  await openMenu()

  expect(
    screen.queryByRole("textbox", { name: "Search workspaces" })
  ).not.toBeInTheDocument()
  expect(workspaceItemNames()).toHaveLength(5)
})

describe("with more than five workspaces", () => {
  beforeEach(() => {
    mockWorkspaces = makeWorkspaces([
      "Alpha",
      "Bravo",
      "Charlie",
      "Delta",
      "Echo",
      "Foxtrot Security",
    ])
  })

  it("filters workspaces case-insensitively", async () => {
    await openMenu()
    const input = screen.getByRole("textbox", { name: "Search workspaces" })

    await userEvent.type(input, "SECUR")

    expect(workspaceItemNames()).toHaveLength(1)
    expect(screen.getByText("Foxtrot Security")).toBeInTheDocument()
  })

  it("focuses the search input when the menu opens", async () => {
    await openMenu()

    expect(
      screen.getByRole("textbox", { name: "Search workspaces" })
    ).toHaveFocus()
  })

  it("moves focus to the first workspace on ArrowDown", async () => {
    await openMenu()

    await userEvent.type(
      screen.getByRole("textbox", { name: "Search workspaces" }),
      "a{ArrowDown}"
    )

    expect(screen.getAllByRole("menuitem")[0]).toHaveFocus()
  })

  it("shows an empty state when nothing matches", async () => {
    await openMenu()

    await userEvent.type(
      screen.getByRole("textbox", { name: "Search workspaces" }),
      "zzz"
    )

    expect(screen.getByText("No workspaces found")).toBeInTheDocument()
  })

  it("navigates to the first match on Enter", async () => {
    await openMenu()

    await userEvent.type(
      screen.getByRole("textbox", { name: "Search workspaces" }),
      "char{Enter}"
    )

    expect(mockPush).toHaveBeenCalledWith(expect.stringContaining("ws-2"))
    expect(
      screen.queryByRole("textbox", { name: "Search workspaces" })
    ).not.toBeInTheDocument()
  })

  it("clears the query when the menu is reopened", async () => {
    await openMenu()
    await userEvent.type(
      screen.getByRole("textbox", { name: "Search workspaces" }),
      "bravo"
    )
    await userEvent.keyboard("{Escape}")

    await userEvent.click(screen.getByRole("button", { name: /alpha/i }))

    expect(
      screen.getByRole("textbox", { name: "Search workspaces" })
    ).toHaveValue("")
    expect(workspaceItemNames()).toHaveLength(6)
  })
})
