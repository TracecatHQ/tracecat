/**
 * @jest-environment jsdom
 */

import { render, screen } from "@testing-library/react"
import type { ReactNode } from "react"
import { AppSidebar } from "@/components/sidebar/app-sidebar"

const mockUseScopeCheck = jest.fn<boolean | undefined, [string]>()
let mockScopes: Record<string, boolean | undefined> = {}

jest.mock("next/navigation", () => ({
  useParams: () => ({}),
  usePathname: () => "/workspaces/workspace-1/workflows",
}))

jest.mock("@/components/auth/scope-guard", () => ({
  useScopeCheck: (scope: string) => mockUseScopeCheck(scope),
}))

jest.mock("@/components/locked-feature-modal", () => ({
  LockedFeatureChip: () => <span>Locked</span>,
  LockedFeatureModal: () => null,
}))

jest.mock("@/components/sidebar/app-menu", () => ({
  AppMenu: () => <div>Workspace menu</div>,
}))

jest.mock("@/components/sidebar/sidebar-user-nav", () => ({
  SidebarUserNav: () => <div>User nav</div>,
}))

jest.mock("@/components/ui/collapsible", () => ({
  Collapsible: ({ children }: { children: ReactNode }) => <div>{children}</div>,
  CollapsibleContent: ({ children }: { children: ReactNode }) => (
    <div>{children}</div>
  ),
  CollapsibleTrigger: ({ children }: { children: ReactNode }) => (
    <button type="button">{children}</button>
  ),
}))

jest.mock("@/components/ui/sidebar", () => ({
  Sidebar: ({ children }: { children: ReactNode }) => <aside>{children}</aside>,
  SidebarContent: ({ children }: { children: ReactNode }) => (
    <div>{children}</div>
  ),
  SidebarFooter: ({ children }: { children: ReactNode }) => (
    <div>{children}</div>
  ),
  SidebarGroup: ({ children }: { children: ReactNode }) => (
    <div>{children}</div>
  ),
  SidebarGroupContent: ({ children }: { children: ReactNode }) => (
    <div>{children}</div>
  ),
  SidebarGroupLabel: ({ children }: { children: ReactNode }) => (
    <div>{children}</div>
  ),
  SidebarHeader: ({ children }: { children: ReactNode }) => (
    <div>{children}</div>
  ),
  SidebarMenu: ({ children }: { children: ReactNode }) => <ul>{children}</ul>,
  SidebarMenuBadge: ({ children }: { children: ReactNode }) => (
    <span>{children}</span>
  ),
  SidebarMenuButton: ({ children }: { children: ReactNode }) => (
    <div>{children}</div>
  ),
  SidebarMenuItem: ({ children }: { children: ReactNode }) => (
    <li>{children}</li>
  ),
  SidebarMenuSub: ({ children }: { children: ReactNode }) => (
    <ul>{children}</ul>
  ),
  SidebarMenuSubButton: ({ children }: { children: ReactNode }) => (
    <div>{children}</div>
  ),
  SidebarMenuSubItem: ({ children }: { children: ReactNode }) => (
    <li>{children}</li>
  ),
  SidebarRail: () => null,
  useSidebar: () => ({ setOpen: jest.fn() }),
}))

jest.mock("@/hooks/use-entitlements", () => ({
  useEntitlements: () => ({
    hasEntitlement: () => true,
    isLoading: false,
  }),
}))

jest.mock("@/hooks/use-pending-approvals-count", () => ({
  usePendingApprovalsCount: () => ({ data: 0 }),
}))

jest.mock("@/providers/workspace-id", () => ({
  useWorkspaceId: () => "workspace-1",
}))

describe("AppSidebar", () => {
  beforeEach(() => {
    mockUseScopeCheck.mockReset()
    mockScopes = {}
    mockUseScopeCheck.mockImplementation((scope) => mockScopes[scope] ?? false)
  })

  it("hides Chat when the user can execute agents but cannot read them", () => {
    mockScopes = {
      "agent:execute": true,
      "agent:read": false,
      "workflow:read": true,
    }

    render(<AppSidebar />)

    expect(screen.queryByText("Chat")).not.toBeInTheDocument()
    expect(screen.getByText("Workflows")).toBeInTheDocument()
  })

  it("shows Chat when the user can execute and read agents", () => {
    mockScopes = {
      "agent:execute": true,
      "agent:read": true,
    }

    render(<AppSidebar />)

    expect(screen.getByText("Chat")).toBeInTheDocument()
  })
})
