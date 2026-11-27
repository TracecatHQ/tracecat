import { render, screen } from "@testing-library/react"
import { AppSidebar } from "@/components/sidebar/app-sidebar"
import { SidebarProvider } from "@/components/ui/sidebar"
import { TooltipProvider } from "@/components/ui/tooltip"

let scopes: Record<string, boolean | undefined> = {}
let entitlements: string[] = []

jest.mock("next/navigation", () => ({
  usePathname: () => "/workspaces/workspace-1/workflows",
  useSearchParams: () => new URLSearchParams(),
}))
jest.mock("@/components/auth/scope-guard", () => ({
  useScopeCheck: (scope: string | undefined, anyOf?: string[]) =>
    scope === undefined
      ? (anyOf ?? []).some((s) => scopes[s] === true)
      : scopes[scope],
}))
jest.mock("@/hooks/use-entitlements", () => ({
  useEntitlements: () => ({
    hasEntitlement: (key: string) => entitlements.includes(key),
    hasEntitlementData: true,
    isLoading: false,
  }),
}))
jest.mock("@/hooks/use-forms-access", () => ({
  useFormsAccess: () => "enabled",
}))
jest.mock("@/hooks/use-pending-approvals-count", () => ({
  usePendingApprovalsCount: () => ({ data: 0 }),
}))
jest.mock("@/hooks/use-case-views", () => ({
  useCaseViews: () => ({ pinned: [] }),
}))
jest.mock("@/providers/workspace-id", () => ({
  useWorkspaceId: () => "workspace-1",
}))
jest.mock("@/components/sidebar/app-menu", () => ({
  AppMenu: () => null,
}))
jest.mock("@/components/sidebar/sidebar-user-nav", () => ({
  SidebarUserNav: () => null,
}))

beforeAll(() => {
  window.matchMedia = jest.fn().mockReturnValue({
    matches: false,
    addEventListener: jest.fn(),
    removeEventListener: jest.fn(),
  })
})

beforeEach(() => {
  scopes = { "workflow:read": true, "trigger:read": true }
  entitlements = []
})

function renderSidebar() {
  return render(
    <TooltipProvider>
      <SidebarProvider>
        <AppSidebar />
      </SidebarProvider>
    </TooltipProvider>
  )
}

describe("AppSidebar Triggers item", () => {
  it("is shown to an org without the object storage entitlement", () => {
    renderSidebar()

    expect(screen.getByRole("link", { name: "Triggers" })).toHaveAttribute(
      "href",
      "/workspaces/workspace-1/triggers"
    )
  })

  it("is hidden from a user without trigger:read", () => {
    scopes["trigger:read"] = false
    entitlements = ["object_triggers"]

    renderSidebar()

    expect(screen.getByRole("link", { name: "Workflows" })).toBeInTheDocument()
    expect(
      screen.queryByRole("link", { name: "Triggers" })
    ).not.toBeInTheDocument()
  })
})
