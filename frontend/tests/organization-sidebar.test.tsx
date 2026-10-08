import { render, screen } from "@testing-library/react"
import { OrganizationSidebar } from "@/components/sidebar/organization-sidebar"
import { SidebarProvider } from "@/components/ui/sidebar"

jest.mock("next/navigation", () => ({
  usePathname: () => "/organization/vcs",
}))
jest.mock("@/hooks/use-mobile", () => ({ useIsMobile: () => false }))
jest.mock("@/components/auth/scope-guard", () => ({
  useScopeCheck: () => true,
}))
jest.mock("@/hooks/use-entitlements", () => ({
  useEntitlements: () => ({ hasEntitlement: () => true }),
}))

describe("OrganizationSidebar", () => {
  it("labels the VCS route as Git providers", () => {
    render(
      <SidebarProvider>
        <OrganizationSidebar />
      </SidebarProvider>
    )
    expect(screen.getByRole("link", { name: "Git providers" })).toHaveAttribute(
      "href",
      "/organization/vcs"
    )
    expect(screen.queryByRole("link", { name: "Git Sync" })).toBeNull()
  })
})
