import { render, screen, waitFor } from "@testing-library/react"
import type { ReactNode } from "react"
import WorkspaceLayout from "@/app/workspaces/layout"
import { usersGetMyScopes } from "@/client"
import type { FormsAccess } from "@/lib/forms/access"
import { QueryClient, QueryClientProvider } from "@/lib/query"

const WORKSPACE_ID = "workspace-1"
const DEFAULT_WORKSPACES = [{ id: WORKSPACE_ID, name: "Security" }]

type ScopeAnswers = Record<string, boolean | undefined>

let mockPathname = `/workspaces/${WORKSPACE_ID}/workflows`
let mockParams: Record<string, string | undefined> = {}
let mockScopes: ScopeAnswers = {}
// Scope answers for one workspace, when a test needs them to differ.
let mockScopesByWorkspace: Record<string, ScopeAnswers> = {}
let mockSelectedWorkspaceId: string | undefined
let mockWorkspaces = DEFAULT_WORKSPACES
let mockLastWorkspaceId: string | undefined
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
    const answers =
      mockScopesByWorkspace[mockSelectedWorkspaceId ?? ""] ?? mockScopes
    return answers[scope]
  },
}))
jest.mock("@/client", () => ({
  ApiError: class ApiError extends Error {},
  usersGetMyScopes: jest.fn(),
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
    workspaces: mockWorkspaces,
    workspacesLoading: false,
    workspacesError: null,
    setLastWorkspaceId: mockSetLastWorkspaceId,
    getLastWorkspaceId: () => mockLastWorkspaceId,
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
  WorkspaceIdProvider: ({
    workspaceId,
    children,
  }: {
    workspaceId: string
    children: ReactNode
  }) => {
    mockSelectedWorkspaceId = workspaceId
    return <>{children}</>
  },
}))

function renderLayout() {
  const queryClient = new QueryClient({
    defaultOptions: { queries: { retry: false } },
  })
  return render(
    <QueryClientProvider client={queryClient}>
      <WorkspaceLayout>Workspace page</WorkspaceLayout>
    </QueryClientProvider>
  )
}

beforeEach(() => {
  jest.clearAllMocks()
  mockPathname = `/workspaces/${WORKSPACE_ID}/workflows`
  mockParams = { workspaceId: WORKSPACE_ID }
  mockScopes = { "workspace:read": false, "form:read": true }
  mockScopesByWorkspace = {}
  mockSelectedWorkspaceId = undefined
  mockWorkspaces = DEFAULT_WORKSPACES
  mockLastWorkspaceId = undefined
  mockFormsAccess = "enabled"
})

describe("landing with no workspace in the URL", () => {
  const INTAKE: ScopeAnswers = { "workspace:read": false, "form:read": true }
  const BUILDER: ScopeAnswers = { "workspace:read": true, "form:read": true }
  const scopeNames = (answers: ScopeAnswers) =>
    Object.keys(answers).filter((scope) => answers[scope])

  beforeEach(() => {
    mockParams = {}
    mockPathname = "/workspaces"
    mockWorkspaces = [
      { id: "workspace-helpdesk", name: "Helpdesk" },
      { id: "workspace-security", name: "Security" },
    ]
    mockScopesByWorkspace = {
      "workspace-helpdesk": INTAKE,
      "workspace-security": BUILDER,
    }
    jest.mocked(usersGetMyScopes).mockImplementation((({
      workspaceId = "",
    } = {}) =>
      Promise.resolve({
        scopes: scopeNames(mockScopesByWorkspace[workspaceId] ?? {}),
      })) as unknown as typeof usersGetMyScopes)
  })

  it("lands a mixed-role user in the workspace they build in, not on /forms", async () => {
    // No last-viewed cookie, intake-only in the first-listed workspace.
    renderLayout()

    expect(await screen.findByText("Workspace page")).toBeInTheDocument()
    expect(mockSelectedWorkspaceId).toBe("workspace-security")
    expect(mockRouter.replace).not.toHaveBeenCalled()
  })

  it("renders nothing and redirects nowhere while scopes load", () => {
    jest
      .mocked(usersGetMyScopes)
      .mockReturnValue(new Promise(() => {}) as never)

    renderLayout()

    expect(mockSelectedWorkspaceId).toBeUndefined()
    expect(mockRouter.replace).not.toHaveBeenCalled()
    expect(screen.queryByText("Workspace page")).not.toBeInTheDocument()
    expect(screen.queryByText("Access denied")).not.toBeInTheDocument()
  })

  it("still sends a user who is intake-only everywhere to /forms", async () => {
    mockScopesByWorkspace["workspace-security"] = INTAKE

    renderLayout()

    await waitFor(() =>
      expect(mockRouter.replace).toHaveBeenCalledWith("/forms")
    )
    expect(mockSelectedWorkspaceId).toBe("workspace-helpdesk")
    expect(screen.queryByText("Workspace page")).not.toBeInTheDocument()
  })

  it("keeps the last-viewed workspace and asks for no scopes", () => {
    mockLastWorkspaceId = "workspace-helpdesk"
    mockScopesByWorkspace["workspace-helpdesk"] = BUILDER

    renderLayout()

    expect(mockSelectedWorkspaceId).toBe("workspace-helpdesk")
    expect(screen.getByText("Workspace page")).toBeInTheDocument()
    expect(usersGetMyScopes).not.toHaveBeenCalled()
  })

  it("asks for no scopes when the URL names a workspace", () => {
    mockParams = { workspaceId: "workspace-security" }
    mockPathname = "/workspaces/workspace-security/workflows"

    renderLayout()

    expect(screen.getByText("Workspace page")).toBeInTheDocument()
    expect(usersGetMyScopes).not.toHaveBeenCalled()
  })
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

  it.each([
    `/workspaces/${WORKSPACE_ID}/forms`,
    `/workspaces/${WORKSPACE_ID}/forms/form-1`,
  ])("leaves the shell to the Forms route layout on %s", (pathname) => {
    // The Forms pages render their own sidebar and header, as Cases and
    // Tables do. A second shell here would nest one inside the other.
    mockScopes = { "workspace:read": true, "form:read": true }
    mockPathname = pathname

    renderLayout()

    expect(screen.getByText("Workspace page")).toBeInTheDocument()
    expect(
      screen.queryByRole("navigation", { name: "Workspace" })
    ).not.toBeInTheDocument()
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
