import { render, screen } from "@testing-library/react"
import { OrganizationSidebar } from "@/components/sidebar/organization-sidebar"
import { SidebarProvider } from "@/components/ui/sidebar"

let mockScopes: Record<string, boolean> = {}
let mockEntitlements: Record<string, boolean> = {}

jest.mock("next/navigation", () => ({
  usePathname: () => "/organization/vcs",
}))
jest.mock("@/hooks/use-mobile", () => ({ useIsMobile: () => false }))
jest.mock("@/components/auth/scope-guard", () => ({
  useScopeCheck: (scope: string) => mockScopes[scope] ?? false,
}))
jest.mock("@/hooks/use-entitlements", () => ({
  useEntitlements: () => ({
    hasEntitlement: (key: string) => mockEntitlements[key] ?? false,
  }),
}))

function renderSidebar() {
  render(
    <SidebarProvider>
      <OrganizationSidebar />
    </SidebarProvider>
  )
}

function gitProvidersItem() {
  return screen.getByRole("link", { name: "Git providers" }).closest("li")
}

beforeEach(() => {
  mockScopes = { "org:settings:read": true }
  mockEntitlements = { git_sync: true }
})

describe("OrganizationSidebar", () => {
  it("labels the VCS route as Git providers", () => {
    renderSidebar()

    expect(screen.getByRole("link", { name: "Git providers" })).toHaveAttribute(
      "href",
      "/organization/vcs"
    )
    expect(screen.queryByRole("link", { name: /git sync/i })).toBeNull()
    expect(gitProvidersItem()).not.toHaveTextContent("Requires upgrade")
  })

  it("hides Git providers without org settings access", () => {
    mockScopes = {}

    renderSidebar()

    expect(screen.queryByRole("link", { name: "Git providers" })).toBeNull()
  })

  it("locks Git providers when the plan lacks Git Sync", () => {
    mockEntitlements = {}

    renderSidebar()

    expect(gitProvidersItem()).toHaveTextContent("Requires upgrade")
  })
})
