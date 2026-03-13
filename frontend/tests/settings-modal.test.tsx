/**
 * @jest-environment jsdom
 */

import { render, screen, waitFor } from "@testing-library/react"
import type { ReactNode } from "react"
import { SettingsModal } from "@/components/settings/settings-modal"

const mockSetOpen = jest.fn()
const mockSetActiveSection = jest.fn()
const mockLogout = jest.fn()
const mockUseEntitlements = jest.fn()
const mockUseUserScopes = jest.fn()
const mockUseWorkspaceManager = jest.fn()
const mockClearLastWorkspaceId = jest.fn()

let mockScopes = ["*"]
let mockWorkspaceId: string | undefined = "workspace-123"
let mockHasGitSync = false
let mockOpen = true
let mockWorkspaces = [{ id: "workspace-123", name: "Workspace 123" }]
let mockScopesByWorkspaceId: Record<string, string[]> = {}

jest.mock("@/components/settings/settings-modal-context", () => ({
  useSettingsModal: () => ({
    open: mockOpen,
    setOpen: mockSetOpen,
    activeSection: "profile",
    setActiveSection: mockSetActiveSection,
  }),
}))

jest.mock("@/components/settings/profile-settings", () => ({
  ProfileSettings: () => <div>Profile content</div>,
}))

jest.mock("@/components/settings/workspace-settings-container", () => ({
  WorkspaceSettingsContainer: () => <div>Workspace settings content</div>,
}))

jest.mock("@/components/ui/dialog", () => ({
  Dialog: ({ open, children }: { open: boolean; children: ReactNode }) =>
    open ? <div>{children}</div> : null,
  DialogContent: ({
    children,
    className,
  }: {
    children: ReactNode
    className?: string
  }) => <div className={className}>{children}</div>,
  DialogDescription: ({
    children,
    className,
  }: {
    children: ReactNode
    className?: string
  }) => <p className={className}>{children}</p>,
  DialogTitle: ({
    children,
    className,
  }: {
    children: ReactNode
    className?: string
  }) => <h2 className={className}>{children}</h2>,
}))

jest.mock("@/components/ui/separator", () => ({
  Separator: () => <hr />,
}))

jest.mock("@/components/ui/tooltip", () => ({
  TooltipProvider: ({ children }: { children: ReactNode }) => <>{children}</>,
}))

jest.mock("@/hooks/use-auth", () => ({
  useAuthActions: () => ({
    logout: mockLogout,
  }),
}))

jest.mock("@/hooks/use-entitlements", () => ({
  useEntitlements: () => {
    mockUseEntitlements()
    return {
      hasEntitlement: (entitlement: string) =>
        mockHasGitSync && entitlement === "git_sync",
    }
  },
}))

jest.mock("@/lib/hooks", () => ({
  useUserScopes: (workspaceId?: string, options?: { enabled?: boolean }) => {
    mockUseUserScopes(workspaceId, options)
    return {
      userScopes: {
        scopes:
          (workspaceId && mockScopesByWorkspaceId[workspaceId]) ?? mockScopes,
      },
      isLoading: false,
    }
  },
  useWorkspaceManager: () => {
    mockUseWorkspaceManager()
    return {
      clearLastWorkspaceId: mockClearLastWorkspaceId,
      getLastWorkspaceId: () => mockWorkspaceId,
      workspaces: mockWorkspaces,
    }
  },
}))

jest.mock("@/providers/workspace-id", () => ({
  useOptionalWorkspaceId: () => undefined,
}))

describe("SettingsModal workspace navigation", () => {
  beforeEach(() => {
    jest.clearAllMocks()
    mockScopes = ["*"]
    mockWorkspaceId = "workspace-123"
    mockHasGitSync = false
    mockOpen = true
    mockWorkspaces = [{ id: "workspace-123", name: "Workspace 123" }]
    mockScopesByWorkspaceId = {}
  })

  it("shows workspace settings sections for wildcard scopes", () => {
    render(<SettingsModal />)

    expect(screen.getByRole("button", { name: "General" })).toBeInTheDocument()
    expect(
      screen.getByRole("button", { name: "Workflows" })
    ).toBeInTheDocument()
    expect(screen.getByRole("button", { name: "Files" })).toBeInTheDocument()
    expect(screen.getByRole("button", { name: "Git sync" })).toBeInTheDocument()
  })

  it("hides workspace settings sections without workspace update access", () => {
    mockScopes = ["workspace:read"]

    render(<SettingsModal />)

    expect(
      screen.queryByRole("button", { name: "General" })
    ).not.toBeInTheDocument()
    expect(
      screen.queryByRole("button", { name: "Workflows" })
    ).not.toBeInTheDocument()
    expect(
      screen.queryByRole("button", { name: "Files" })
    ).not.toBeInTheDocument()
    expect(
      screen.queryByRole("button", { name: "Git sync" })
    ).not.toBeInTheDocument()
  })

  it("does not run workspace or entitlement hooks while closed", () => {
    mockOpen = false

    render(<SettingsModal />)

    expect(mockUseWorkspaceManager).not.toHaveBeenCalled()
    expect(mockUseUserScopes).not.toHaveBeenCalled()
    expect(mockUseEntitlements).not.toHaveBeenCalled()
  })

  it("falls back to the first accessible workspace when the last viewed cookie is stale", () => {
    mockWorkspaceId = "missing-workspace"
    mockWorkspaces = [
      { id: "workspace-456", name: "Workspace 456" },
      { id: "workspace-789", name: "Workspace 789" },
    ]

    render(<SettingsModal />)

    expect(mockUseUserScopes).toHaveBeenCalledWith("workspace-456", {
      enabled: true,
    })
    expect(mockClearLastWorkspaceId).toHaveBeenCalled()
    expect(screen.getByRole("button", { name: "General" })).toBeInTheDocument()
  })

  it("probes additional workspaces until it finds one with update access", async () => {
    mockWorkspaceId = "workspace-viewer"
    mockWorkspaces = [
      { id: "workspace-admin", name: "Zulu workspace" },
      { id: "workspace-viewer", name: "Alpha workspace" },
    ]
    mockScopesByWorkspaceId = {
      "workspace-admin": ["workspace:update"],
      "workspace-viewer": ["workspace:read"],
    }

    render(<SettingsModal />)

    await waitFor(() => {
      expect(mockUseUserScopes).toHaveBeenCalledWith("workspace-viewer", {
        enabled: true,
      })
      expect(mockUseUserScopes).toHaveBeenCalledWith("workspace-admin", {
        enabled: true,
      })
      expect(
        screen.getByRole("button", { name: "General" })
      ).toBeInTheDocument()
    })
  })
})
