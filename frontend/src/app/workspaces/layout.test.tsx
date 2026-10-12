import { render, screen } from "@testing-library/react"
import type { ReactNode } from "react"
import WorkspaceLayout from "@/app/workspaces/layout"
import type { FormsAccess } from "@/lib/forms/access"

const WORKSPACE_ID = "workspace-1"

let mockPathname = `/workspaces/${WORKSPACE_ID}/workflows`
let mockParams: Record<string, string | undefined> = {}
let mockScopes: Record<string, boolean | undefined> = {}
let mockFormsAccess: FormsAccess = "enabled"
const mockRouter = { replace: jest.fn() }
const mockSetLastWorkspaceId = jest.fn()

function passthrough({ children }: { children: ReactNode }) {
  return <>{children}</>
}

jest.mock("next/navigation", () => ({
  useParams: () => mockParams,
  usePathname: () => mockPathname,
  useRouter: () => mockRouter,
}))
jest.mock("next/image", () => ({
  __esModule: true,
  default: ({ alt }: { alt: string }) => <span>{alt}</span>,
}))
jest.mock("public/icon.png", () => ({ __esModule: true, default: "icon.png" }))
jest.mock("@xyflow/react", () => ({ ReactFlowProvider: passthrough }))
jest.mock("@/components/auth/scope-guard", () => ({
  // Reading any scope the rule must not depend on fails the test.
  useScopeCheck: (scope: string) => {
    if (scope.startsWith("agent:") || scope.startsWith("org:")) {
      throw new Error(`The workspace shell must not read ${scope}`)
    }
    return mockScopes[scope]
  },
}))
jest.mock("@/components/auth/no-organization-access", () => ({
  NoOrganizationAccess: () => null,
}))
jest.mock("@/components/cases/case-selection-context", () => ({
  CaseSelectionProvider: passthrough,
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
jest.mock("@/components/ui/sidebar", () => ({
  SidebarProvider: passthrough,
  SidebarInset: passthrough,
}))
jest.mock("@/hooks/use-auth", () => ({
  useAuth: () => ({ user: { id: "user-1" }, userIsLoading: false }),
  useAuthActions: () => ({ logout: jest.fn() }),
}))
jest.mock("@/hooks/use-entitlements", () => ({
  useEntitlements: () => ({
    hasEntitlement: () => false,
    hasEntitlementData: true,
    isLoading: false,
  }),
}))
jest.mock("@/hooks/use-forms-access", () => ({
  useFormsAccess: () => mockFormsAccess,
}))
jest.mock("@/lib/hooks", () => ({
  useWorkspaceManager: () => ({
    workspaces: [{ id: "workspace-1", name: "Security" }],
    workspacesLoading: false,
    workspacesError: null,
    setLastWorkspaceId: mockSetLastWorkspaceId,
    getLastWorkspaceId: () => undefined,
    createWorkspace: jest.fn(),
  }),
}))
jest.mock("@/providers/agent-preset-detail", () => ({
  AgentPresetDetailProvider: passthrough,
}))
jest.mock("@/providers/builder", () => ({
  WorkflowBuilderProvider: passthrough,
}))
jest.mock("@/providers/scopes", () => ({ ScopeProvider: passthrough }))
jest.mock("@/providers/skills-studio", () => ({
  SkillsStudioProvider: passthrough,
}))
jest.mock("@/providers/workflow", () => ({ WorkflowProvider: passthrough }))
jest.mock("@/providers/workspace-id", () => ({
  WorkspaceIdProvider: passthrough,
}))

function renderLayout() {
  return render(<WorkspaceLayout>Workspace page</WorkspaceLayout>)
}

beforeEach(() => {
  jest.clearAllMocks()
  mockPathname = `/workspaces/${WORKSPACE_ID}/workflows`
  mockParams = { workspaceId: WORKSPACE_ID }
  mockScopes = { "workspace:read": false, "form:read": true }
  mockFormsAccess = "enabled"
})

describe("intake-only redirect", () => {
  it("sends an intake-only user to /forms", () => {
    renderLayout()

    expect(mockRouter.replace).toHaveBeenCalledWith("/forms")
    expect(screen.queryByText("Workspace page")).not.toBeInTheDocument()
    expect(screen.queryByText("Access denied")).not.toBeInTheDocument()
  })

  it("maps a form deep link to the requester page of that form", () => {
    mockPathname = `/workspaces/${WORKSPACE_ID}/forms/form-1`

    renderLayout()

    expect(mockRouter.replace).toHaveBeenCalledWith("/forms/form-1")
  })

  it("redirects without the entitlement, where /forms explains", () => {
    mockFormsAccess = "locked"

    renderLayout()

    expect(mockRouter.replace).toHaveBeenCalledWith("/forms")
  })

  it("keeps access denied with the feature flag off", () => {
    mockFormsAccess = "hidden"

    renderLayout()

    expect(mockRouter.replace).not.toHaveBeenCalled()
    expect(screen.getByText("Access denied")).toBeInTheDocument()
  })

  it("keeps access denied for a user who cannot read forms either", () => {
    mockScopes = { "workspace:read": false, "form:read": false }

    renderLayout()

    expect(mockRouter.replace).not.toHaveBeenCalled()
    expect(screen.getByText("Access denied")).toBeInTheDocument()
  })

  it("waits for the flag instead of flashing access denied", () => {
    mockFormsAccess = "loading"

    renderLayout()

    expect(mockRouter.replace).not.toHaveBeenCalled()
    expect(screen.queryByText("Access denied")).not.toBeInTheDocument()
    expect(screen.queryByText("Workspace page")).not.toBeInTheDocument()
  })

  it("gives a viewer the workspace shell", () => {
    mockScopes = { "workspace:read": true, "form:read": true }

    renderLayout()

    expect(mockRouter.replace).not.toHaveBeenCalled()
    expect(screen.getByText("Workspace page")).toBeInTheDocument()
    expect(screen.getByRole("navigation", { name: "Workspace" })).toBeVisible()
  })

  it("gives a viewer the shell while the flag is still loading", () => {
    mockScopes = { "workspace:read": true, "form:read": true }
    mockFormsAccess = "loading"

    renderLayout()

    expect(mockRouter.replace).not.toHaveBeenCalled()
    expect(screen.getByText("Workspace page")).toBeInTheDocument()
  })

  it("does nothing while scopes are loading", () => {
    mockScopes = {}

    renderLayout()

    expect(mockRouter.replace).not.toHaveBeenCalled()
    expect(mockSetLastWorkspaceId).not.toHaveBeenCalled()
    expect(screen.queryByText("Access denied")).not.toBeInTheDocument()
    expect(screen.queryByText("Workspace page")).not.toBeInTheDocument()
  })
})

describe("last-viewed workspace", () => {
  it("is remembered for a user who can read the workspace", () => {
    mockScopes = { "workspace:read": true, "form:read": true }

    renderLayout()

    expect(mockSetLastWorkspaceId).toHaveBeenCalledWith(WORKSPACE_ID)
  })

  it("is not remembered for an intake-only user", () => {
    renderLayout()

    expect(mockSetLastWorkspaceId).not.toHaveBeenCalled()
  })

  it("is not remembered when the URL names no workspace", () => {
    mockScopes = { "workspace:read": true, "form:read": true }
    mockParams = {}
    mockPathname = "/workspaces"

    renderLayout()

    expect(mockSetLastWorkspaceId).not.toHaveBeenCalled()
  })
})
