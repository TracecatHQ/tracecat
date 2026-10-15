/**
 * @jest-environment jsdom
 */

import { fireEvent, render, screen, waitFor } from "@testing-library/react"
import type { ReactNode } from "react"
import WorkspaceLayout from "@/app/workspaces/layout"

const mockRouterReplace = jest.fn()
const mockLogout = jest.fn()
const mockCreateWorkspace = jest.fn()
const mockUseScopeCheck = jest.fn<boolean | undefined, [string]>()

let mockParams: Record<string, string | undefined> = {}
let mockPathname = "/workspaces"
let mockUser: { isSuperuser: boolean } | null = { isSuperuser: false }
let mockWorkspaces: { id: string; name: string }[] | undefined = []
let mockWorkspacesLoading = false
let mockWorkspacesError: Error | null = null
let mockScopes: Record<string, boolean | undefined> = {}
let mockEntitlements: Record<string, boolean> = {}
let mockEntitlementsKnown = true
let mockEntitlementsLoading = false
let mockFormsAccess = "enabled"
const mockSetLastWorkspaceId = jest.fn()

jest.mock("next/image", () => ({
  __esModule: true,
  default: (props: { alt: string; className?: string }) => (
    <img alt={props.alt} className={props.className} />
  ),
}))

jest.mock("next/navigation", () => ({
  useParams: () => mockParams,
  usePathname: () => mockPathname,
  useRouter: () => ({ replace: mockRouterReplace }),
}))

jest.mock("@/components/auth/no-organization-access", () => ({
  NoOrganizationAccess: () => <div>No organization access</div>,
}))

jest.mock("@/components/auth/scope-guard", () => ({
  useScopeCheck: (scope: string) => mockUseScopeCheck(scope),
}))

jest.mock("@/components/loading/spinner", () => ({
  CenteredSpinner: () => <div>Loading</div>,
}))

jest.mock("@/components/nav/controls-header", () => ({
  ControlsHeader: () => null,
}))

jest.mock("@/components/nav/dynamic-nav", () => ({
  DynamicNavbar: () => null,
}))

jest.mock("@/components/settings/settings-modal", () => ({
  SettingsModal: () => null,
}))

jest.mock("@/components/sidebar/app-sidebar", () => ({
  AppSidebar: () => <nav aria-label="Workspace" />,
}))

jest.mock("@/components/ui/button", () => ({
  Button: ({
    asChild,
    children,
    disabled,
    onClick,
    type,
  }: {
    asChild?: boolean
    children: ReactNode
    disabled?: boolean
    onClick?: () => void
    type?: "button" | "submit" | "reset"
  }) =>
    asChild ? (
      <>{children}</>
    ) : (
      <button type={type ?? "button"} disabled={disabled} onClick={onClick}>
        {children}
      </button>
    ),
}))

jest.mock("@/components/ui/sidebar", () => ({
  SidebarInset: ({ children }: { children: ReactNode }) => <>{children}</>,
  SidebarProvider: ({ children }: { children: ReactNode }) => <>{children}</>,
}))

jest.mock("@/hooks/use-auth", () => ({
  useAuth: () => ({ user: mockUser, userIsLoading: false }),
  useAuthActions: () => ({ logout: mockLogout }),
}))

jest.mock("@/hooks/use-entitlements", () => ({
  useEntitlements: () => ({
    hasEntitlement: (key: string) => mockEntitlements[key] ?? false,
    hasEntitlementData: mockEntitlementsKnown,
    isLoading: mockEntitlementsLoading,
  }),
}))

jest.mock("@/hooks/use-forms-access", () => ({
  useFormsAccess: () => mockFormsAccess,
}))

// The landing choice reads scopes through React Query and has its own tests.
jest.mock("@/hooks/use-landing-workspace", () => ({
  useLandingWorkspace: ({ workspaces }: { workspaces?: { id: string }[] }) =>
    workspaces?.[0]?.id,
}))

jest.mock("@/lib/hooks", () => ({
  useWorkspaceManager: () => ({
    workspaces: mockWorkspaces,
    workspacesLoading: mockWorkspacesLoading,
    workspacesError: mockWorkspacesError,
    setLastWorkspaceId: mockSetLastWorkspaceId,
    getLastWorkspaceId: jest.fn(),
    createWorkspace: mockCreateWorkspace,
  }),
}))

jest.mock("@/providers/agent-preset-detail", () => ({
  AgentPresetDetailProvider: ({ children }: { children: ReactNode }) => (
    <>{children}</>
  ),
}))

jest.mock("@/providers/builder", () => ({
  WorkflowBuilderProvider: ({ children }: { children: ReactNode }) => (
    <>{children}</>
  ),
}))

jest.mock("@/providers/scopes", () => ({
  ScopeProvider: ({ children }: { children: ReactNode }) => <>{children}</>,
}))

jest.mock("@/providers/skills-studio", () => ({
  SkillsStudioProvider: ({ children }: { children: ReactNode }) => (
    <>{children}</>
  ),
}))

jest.mock("@/providers/workflow", () => ({
  WorkflowProvider: ({ children }: { children: ReactNode }) => <>{children}</>,
}))

jest.mock("@/providers/workspace-id", () => ({
  WorkspaceIdProvider: ({ children }: { children: ReactNode }) => (
    <>{children}</>
  ),
}))

jest.mock("@xyflow/react", () => ({
  ReactFlowProvider: ({ children }: { children: ReactNode }) => <>{children}</>,
}))

describe("WorkspaceLayout empty workspace access", () => {
  beforeEach(() => {
    jest.clearAllMocks()
    mockParams = {}
    mockPathname = "/workspaces"
    mockUser = { isSuperuser: false }
    mockWorkspaces = []
    mockWorkspacesLoading = false
    mockWorkspacesError = null
    mockScopes = {
      "org:workspace:read": false,
      "workspace:create": true,
    }
    mockEntitlements = { multi_workspace: false }
    mockEntitlementsKnown = true
    mockEntitlementsLoading = false
    mockCreateWorkspace.mockResolvedValue({
      id: "workspace-1",
      name: "New Workspace",
    })
    mockUseScopeCheck.mockImplementation((scope) => mockScopes[scope] ?? false)
  })

  it("allows an organization reader to create its first workspace", async () => {
    mockScopes["org:workspace:read"] = true

    render(
      <WorkspaceLayout>
        <div>Workspace content</div>
      </WorkspaceLayout>
    )

    expect(
      screen.getByText(
        "There are no workspaces yet. Create one to get started."
      )
    ).toBeInTheDocument()
    fireEvent.click(screen.getByRole("button", { name: "Create workspace" }))

    await waitFor(() => {
      expect(mockCreateWorkspace).toHaveBeenCalledWith({
        name: "New Workspace",
      })
      expect(mockRouterReplace).toHaveBeenCalledWith("/workspaces/workspace-1")
    })
  })

  it("shows the contact administrator state to a member without access", () => {
    render(
      <WorkspaceLayout>
        <div>Workspace content</div>
      </WorkspaceLayout>
    )

    expect(
      screen.getByText(
        "You are not a member of any workspace. Please contact your administrator."
      )
    ).toBeInTheDocument()
    expect(
      screen.queryByRole("button", { name: "Create workspace" })
    ).not.toBeInTheDocument()
  })

  it("allows a member to create when multi-workspace is confirmed", () => {
    mockEntitlements = { multi_workspace: true }

    render(
      <WorkspaceLayout>
        <div>Workspace content</div>
      </WorkspaceLayout>
    )

    expect(
      screen.getByRole("button", { name: "Create workspace" })
    ).toBeInTheDocument()
  })

  it.each([
    { isLoading: true, hasData: false },
    { isLoading: false, hasData: false },
  ])(
    "keeps a member's empty-list state neutral while entitlement data is unavailable",
    ({ isLoading, hasData }) => {
      mockEntitlements = {}
      mockEntitlementsLoading = isLoading
      mockEntitlementsKnown = hasData

      render(
        <WorkspaceLayout>
          <div>Workspace content</div>
        </WorkspaceLayout>
      )

      expect(
        screen.getByText(
          "You are not a member of any workspace. Please contact your administrator."
        )
      ).toBeInTheDocument()
      expect(
        screen.queryByRole("button", { name: "Create workspace" })
      ).not.toBeInTheDocument()
      expect(screen.queryByText(/upgrade/i)).not.toBeInTheDocument()
    }
  )
})

describe("WorkspaceLayout forms intake", () => {
  beforeEach(() => {
    jest.clearAllMocks()
    mockWorkspaces = [{ id: "workspace-1", name: "Security" }]
    mockParams = { workspaceId: "workspace-1" }
    mockPathname = "/workspaces/workspace-1/workflows"
    mockScopes = { "workspace:read": false, "form:read": true }
    mockFormsAccess = "enabled"
    mockUseScopeCheck.mockImplementation((scope) => mockScopes[scope])
  })

  const renderLayout = () =>
    render(
      <WorkspaceLayout>
        <div>Workspace content</div>
      </WorkspaceLayout>
    )

  it.each([
    ["/workspaces/workspace-1/workflows", "enabled", "/forms"],
    ["/workspaces/workspace-1/forms/form-1", "locked", "/forms/form-1"],
  ])(
    "sends an intake-only user on %s (%s) to %s",
    (pathname, access, target) => {
      mockPathname = pathname
      mockFormsAccess = access

      renderLayout()

      expect(mockRouterReplace).toHaveBeenCalledWith(target)
      expect(screen.queryByText("Access denied")).not.toBeInTheDocument()
      // Remembering this workspace would bounce the user here on every visit.
      expect(mockSetLastWorkspaceId).not.toHaveBeenCalled()
    }
  )

  it.each([
    ["hidden", true, "Access denied"],
    ["enabled", false, "Access denied"],
    ["loading", true, "Loading"],
  ])(
    "does not redirect when Forms is %s and form:read is %p",
    (access, canReadForms, shown) => {
      mockFormsAccess = access
      mockScopes["form:read"] = canReadForms

      renderLayout()

      expect(mockRouterReplace).not.toHaveBeenCalled()
      expect(screen.getByText(shown)).toBeInTheDocument()
    }
  )

  it.each([
    ["/workspaces/workspace-1/workflows", true],
    ["/workspaces/workspace-1/forms/form-1", false],
  ])("gives a reader on %s the shell: %p", (pathname, withShell) => {
    mockPathname = pathname
    mockScopes = { "workspace:read": true, "form:read": true }

    renderLayout()

    expect(screen.getByText("Workspace content")).toBeInTheDocument()
    expect(Boolean(screen.queryByRole("navigation"))).toBe(withShell)
    expect(mockSetLastWorkspaceId).toHaveBeenCalledWith("workspace-1")
  })
})
