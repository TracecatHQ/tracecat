/**
 * @jest-environment jsdom
 */

import { render, screen, waitFor } from "@testing-library/react"
import WorkspacePage from "@/app/workspaces/[workspaceId]/page"
import type { FormsAccess } from "@/lib/forms/access"
import { getWorkspaceLandingPath } from "@/lib/workspace-navigation"

const mockRouterReplace = jest.fn()
const mockUseScopeCheck = jest.fn<boolean | undefined, [string]>()
const mockHasEntitlement = jest.fn<boolean, [string]>()
let mockScopes: Record<string, boolean | undefined> = {}
let mockEntitlements: Record<string, boolean> = {}
let mockEntitlementsLoading = false
let mockFormsAccess: FormsAccess = "hidden"
// `useScopeCheck(undefined, [...])` asks for any of the form builder scopes.
const FORM_BUILDER_SCOPES = ["form:update", "form:submission:read"]

jest.mock("next/navigation", () => ({
  useRouter: () => ({ replace: mockRouterReplace }),
}))

jest.mock("next/image", () => ({
  __esModule: true,
  default: (props: {
    src: string
    alt: string
    className?: string
  }): JSX.Element => <img alt={props.alt} className={props.className} />,
}))

jest.mock("@/components/auth/scope-guard", () => ({
  useScopeCheck: (scope: string) => mockUseScopeCheck(scope),
}))

jest.mock("@/components/loading/spinner", () => ({
  CenteredSpinner: () => <div>Loading</div>,
}))

jest.mock("@/hooks", () => ({
  useEntitlements: () => ({
    hasEntitlement: mockHasEntitlement,
    isLoading: mockEntitlementsLoading,
  }),
}))

jest.mock("@/hooks/use-forms-access", () => ({
  useFormsAccess: () => mockFormsAccess,
}))

jest.mock("@/providers/workspace-id", () => ({
  useWorkspaceId: () => "workspace-1",
}))

describe("WorkspacePage", () => {
  beforeEach(() => {
    mockRouterReplace.mockReset()
    mockUseScopeCheck.mockReset()
    mockHasEntitlement.mockReset()
    mockScopes = {}
    mockEntitlements = {}
    mockEntitlementsLoading = false
    mockFormsAccess = "hidden"
    mockUseScopeCheck.mockImplementation((scope) => {
      if (scope === undefined) {
        return FORM_BUILDER_SCOPES.some((name) => mockScopes[name] === true)
      }
      return mockScopes[scope] ?? false
    })
    mockHasEntitlement.mockImplementation(
      (entitlement) => mockEntitlements[entitlement] ?? false
    )
  })

  it("does not redirect service-account-only users into the workspace shell", () => {
    mockScopes = {
      "workspace:service_account:read": true,
      "workspace:read": false,
    }
    mockHasEntitlement.mockReturnValue(true)

    render(<WorkspacePage />)

    expect(mockRouterReplace).not.toHaveBeenCalled()
    expect(screen.getByText("No accessible pages")).toBeInTheDocument()
  })

  it("redirects workspace-readable service-account users to service accounts", async () => {
    mockScopes = {
      "workspace:service_account:read": true,
      "workspace:read": true,
    }
    mockHasEntitlement.mockImplementation(
      (entitlement) => entitlement === "service_accounts"
    )

    render(<WorkspacePage />)

    await waitFor(() => {
      expect(mockRouterReplace).toHaveBeenCalledWith(
        "/workspaces/workspace-1/service-accounts"
      )
    })
  })

  it("redirects to Chat when the workspace shell is readable", async () => {
    expect(getWorkspaceLandingPath("workspace-1")).toBe(
      "/workspaces/workspace-1"
    )
    mockScopes = {
      "agent:execute": true,
      "agent:read": true,
      "workspace:read": true,
    }
    mockEntitlements = {
      workspace_chat: true,
    }

    render(<WorkspacePage />)

    await waitFor(() => {
      expect(mockRouterReplace).toHaveBeenCalledWith(
        "/workspaces/workspace-1/chat"
      )
    })
  })

  it("falls back to workflows when Chat is not entitled", async () => {
    mockScopes = {
      "workspace:read": true,
      "workflow:read": true,
      "agent:read": true,
      "agent:execute": true,
    }
    mockEntitlements = {
      workspace_chat: false,
    }

    render(<WorkspacePage />)

    await waitFor(() => {
      expect(mockRouterReplace).toHaveBeenCalledWith(
        "/workspaces/workspace-1/workflows"
      )
    })
  })

  it("falls back to agents when Chat, workflows, and cases are unavailable", async () => {
    mockScopes = {
      "workspace:read": true,
      "agent:read": true,
    }
    mockEntitlements = {
      workspace_chat: true,
    }

    render(<WorkspacePage />)

    await waitFor(() => {
      expect(mockRouterReplace).toHaveBeenCalledWith(
        "/workspaces/workspace-1/agents"
      )
    })
  })

  it("lands a forms builder on Forms ahead of cases", async () => {
    mockFormsAccess = "enabled"
    mockScopes = {
      "workspace:read": true,
      "case:read": true,
      "form:read": true,
      "form:submission:read": true,
    }

    render(<WorkspacePage />)

    await waitFor(() => {
      expect(mockRouterReplace).toHaveBeenCalledWith(
        "/workspaces/workspace-1/forms"
      )
    })
  })

  it("keeps workflows ahead of Forms", async () => {
    mockFormsAccess = "enabled"
    mockScopes = {
      "workspace:read": true,
      "workflow:read": true,
      "form:read": true,
      "form:update": true,
    }

    render(<WorkspacePage />)

    await waitFor(() => {
      expect(mockRouterReplace).toHaveBeenCalledWith(
        "/workspaces/workspace-1/workflows"
      )
    })
  })

  it.each([
    ["the Forms flag is off", "hidden", { "form:submission:read": true }],
    ["the user can only fill in forms", "enabled", {}],
  ] as const)("skips Forms when %s", async (_name, access, formScopes) => {
    mockFormsAccess = access
    mockScopes = {
      "workspace:read": true,
      "case:read": true,
      "form:read": true,
      ...formScopes,
    }

    render(<WorkspacePage />)

    await waitFor(() => {
      expect(mockRouterReplace).toHaveBeenCalledWith(
        "/workspaces/workspace-1/cases"
      )
    })
  })

  it("waits for the Forms flag before selecting a landing page", () => {
    mockFormsAccess = "loading"
    mockScopes = { "workspace:read": true, "workflow:read": true }

    render(<WorkspacePage />)

    expect(mockRouterReplace).not.toHaveBeenCalled()
    expect(screen.getByText("Loading")).toBeInTheDocument()
  })

  it("waits for entitlements before selecting a landing page", () => {
    mockEntitlementsLoading = true
    mockScopes = {
      "workflow:read": true,
    }

    render(<WorkspacePage />)

    expect(mockRouterReplace).not.toHaveBeenCalled()
    expect(screen.getByText("Loading")).toBeInTheDocument()
  })
})
