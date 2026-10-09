/**
 * @jest-environment jsdom
 */

import { render, screen } from "@testing-library/react"
import type { ReactNode } from "react"
import { AppSidebar } from "@/components/sidebar/app-sidebar"

const mockUseScopeCheck = jest.fn<
  boolean | undefined,
  [string | undefined, string[] | undefined]
>()
let mockPathname = "/workspaces/workspace-1/workflows"
let mockScopes: Record<string, boolean | undefined> = {}
let mockEntitlements: Record<string, boolean> = {}
let mockEntitlementsIsLoading = false
let mockHasEntitlementData = true

jest.mock("next/navigation", () => ({
  useParams: () => ({}),
  usePathname: () => mockPathname,
}))

jest.mock("@/components/auth/scope-guard", () => ({
  useScopeCheck: (scope: string | undefined, anyOf?: string[]) =>
    mockUseScopeCheck(scope, anyOf),
}))

jest.mock("@/components/locked-feature-modal", () => ({
  LockedFeatureChip: () => <span>Locked</span>,
  LockedFeatureModal: () => null,
}))

jest.mock("@/components/sidebar/app-menu", () => ({
  AppMenu: () => <div>Workspace menu</div>,
}))

jest.mock("@/components/sidebar/sidebar-user-nav", () => ({
  SidebarUserNav: ({
    manageItems,
  }: {
    manageItems: {
      title: string
      href: string
      isActive?: boolean
    }[]
  }) => (
    <nav>
      {manageItems.map((item) => (
        <a
          href={item.href}
          data-active={item.isActive ? "true" : "false"}
          key={item.href}
        >
          {item.title}
        </a>
      ))}
    </nav>
  ),
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
  SidebarMenuButton: ({
    asChild,
    children,
    disabled,
    isActive,
    type,
  }: {
    asChild?: boolean
    children: ReactNode
    disabled?: boolean
    isActive?: boolean
    type?: "button" | "submit" | "reset"
  }) =>
    asChild ? (
      <div data-active={isActive ? "true" : "false"}>{children}</div>
    ) : (
      <button type={type ?? "button"} disabled={disabled}>
        {children}
      </button>
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
    hasEntitlement: (entitlement: string) =>
      mockEntitlements[entitlement] ?? false,
    isLoading: mockEntitlementsIsLoading,
    hasEntitlementData: mockHasEntitlementData,
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
    mockPathname = "/workspaces/workspace-1/workflows"
    mockScopes = {}
    mockEntitlements = {
      agent_addons: true,
      service_accounts: true,
      workspace_chat: true,
    }
    mockEntitlementsIsLoading = false
    mockHasEntitlementData = true
    mockUseScopeCheck.mockImplementation((scope, anyOf) =>
      scope
        ? (mockScopes[scope] ?? false)
        : (anyOf ?? []).some((candidate) => mockScopes[candidate])
    )
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

  it("does not show entitlement locks while entitlements are loading", () => {
    mockScopes = {
      "agent:execute": true,
      "agent:read": true,
    }
    mockEntitlements = {}
    mockEntitlementsIsLoading = true
    mockHasEntitlementData = false

    render(<AppSidebar />)

    expect(screen.getByText("Chat")).toBeInTheDocument()
    expect(screen.queryByRole("link", { name: /Chat/ })).not.toBeInTheDocument()
    expect(screen.getByRole("button", { name: /Chat/ })).toBeDisabled()
    expect(screen.queryByText("Locked")).not.toBeInTheDocument()
  })

  it("shows entitlement locks after entitlement data confirms a feature is unavailable", () => {
    mockScopes = {
      "agent:execute": true,
      "agent:read": true,
    }
    mockEntitlements = {
      agent_addons: true,
      service_accounts: true,
      workspace_chat: false,
    }

    render(<AppSidebar />)

    expect(screen.getByText("Chat")).toBeInTheDocument()
    expect(screen.getAllByText("Locked")).toHaveLength(1)
  })

  it("keeps Git sync visible with a lock when the plan lacks it", () => {
    mockScopes = { "workspace:update": true }

    render(<AppSidebar />)

    expect(screen.getByRole("link", { name: "Git sync" })).toHaveAttribute(
      "href",
      "/workspaces/workspace-1/git-sync"
    )
    expect(screen.getByText("Requires upgrade")).toBeInTheDocument()
  })

  it("shows Git sync without a lock when the plan includes it", () => {
    mockScopes = { "workspace_sync:sync": true }
    mockEntitlements = { git_sync: true }

    render(<AppSidebar />)

    expect(screen.getByRole("link", { name: "Git sync" })).toBeInTheDocument()
    expect(screen.queryByText("Requires upgrade")).not.toBeInTheDocument()
  })

  it("hides Git sync without sync or connection access", () => {
    mockScopes = { "workspace:read": true }

    render(<AppSidebar />)

    expect(
      screen.queryByRole("link", { name: "Git sync" })
    ).not.toBeInTheDocument()
    expect(screen.queryByText("Requires upgrade")).not.toBeInTheDocument()
  })

  it("only highlights MCP servers on the MCP servers page", () => {
    mockPathname = "/workspaces/workspace-1/mcp-servers"
    mockScopes = {
      "integration:read": true,
      "workspace:read": true,
    }

    render(<AppSidebar />)

    expect(
      screen.getByRole("link", { name: "MCP servers" }).closest("[data-active]")
    ).toHaveAttribute("data-active", "true")
    expect(screen.getByRole("link", { name: "MCP access" })).toHaveAttribute(
      "data-active",
      "false"
    )
  })
})
