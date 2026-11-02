import { render, screen } from "@testing-library/react"
import type { ReactNode } from "react"
import OrganizationLayout from "@/app/organization/layout"

let mockPathname = "/organization/settings/scim"
let mockScopes: string[] = []
const mockRouter = { push: jest.fn() }

jest.mock("next/navigation", () => ({
  usePathname: () => mockPathname,
  useRouter: () => mockRouter,
}))
jest.mock("@/hooks/use-auth", () => ({
  useAuth: () => ({ user: { id: "user" }, userIsLoading: false }),
}))
jest.mock("@/lib/hooks", () => ({
  useUserScopes: () => ({
    userScopes: { scopes: mockScopes },
    isLoading: false,
  }),
}))
jest.mock("@/providers/scopes", () => ({
  ScopeProvider: ({ children }: { children: ReactNode }) => children,
}))
jest.mock("@/components/sidebar/organization-sidebar", () => ({
  OrganizationSidebar: () => null,
}))
jest.mock("@/components/ui/sidebar", () => ({
  SidebarProvider: ({ children }: { children: ReactNode }) => children,
  SidebarInset: ({ children }: { children: ReactNode }) => children,
}))

test.each([
  {
    path: "/organization/settings/scim",
    scope: "org:scim:manage",
    allowed: true,
  },
  { path: "/organization/settings/scim", scope: "org:update", allowed: false },
  {
    path: "/organization/settings/scim",
    scope: "org:rbac:update",
    allowed: false,
  },
  { path: "/organization/members", scope: "org:scim:manage", allowed: false },
  { path: "/organization/members", scope: "org:update", allowed: true },
  {
    path: "/organization/settings/scim-other",
    scope: "org:scim:manage",
    allowed: false,
  },
])(
  "organization route authority: $path / $scope",
  ({ path, scope, allowed }) => {
    mockRouter.push.mockClear()
    mockPathname = path
    mockScopes = [scope]
    render(<OrganizationLayout>Protected page</OrganizationLayout>)
    if (allowed) {
      expect(screen.getByText("Protected page")).toBeInTheDocument()
      expect(mockRouter.push).not.toHaveBeenCalled()
    } else {
      expect(screen.queryByText("Protected page")).not.toBeInTheDocument()
      expect(mockRouter.push).toHaveBeenCalledWith("/")
    }
  }
)
