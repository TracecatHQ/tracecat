/**
 * @jest-environment jsdom
 */

import { render, screen, waitFor } from "@testing-library/react"
import type { ReactNode } from "react"
import { SettingsModal } from "@/components/settings/settings-modal"
import type { SettingsSection } from "@/components/settings/settings-modal-context"

const mockSetOpen = jest.fn()
const mockSetActiveSection = jest.fn()
const mockLogout = jest.fn()
const mockUseEntitlements = jest.fn()
const mockUseUserScopes = jest.fn()
const mockUseWorkspaceManager = jest.fn()
const mockClearLastWorkspaceId = jest.fn()
const mockWorkspaceSettingsContainer = jest.fn()

let mockScopes = ["*"]
let mockContextWorkspaceId: string | undefined = "workspace-123"
let mockLastWorkspaceId: string | undefined
let mockHasGitSync = false
let mockOpen = true
let mockActiveSection: SettingsSection = "profile"
let mockWorkspaces = [{ id: "workspace-123", name: "Workspace 123" }]
let mockScopesByWorkspaceId: Record<string, string[]> = {}

jest.mock("@/components/settings/settings-modal-context", () => ({
  useSettingsModal: () => ({
    open: mockOpen,
    setOpen: mockSetOpen,
    activeSection: mockActiveSection,
    setActiveSection: mockSetActiveSection,
  }),
}))

jest.mock("@/components/settings/profile-settings", () => ({
  ProfileSettings: () => <div>Profile content</div>,
}))

jest.mock("@/components/settings/workspace-settings-container", () => ({
  WorkspaceSettingsContainer: ({
    workspaceId,
    activeSection,
  }: {
    workspaceId: string
    activeSection: SettingsSection
  }) => {
    mockWorkspaceSettingsContainer({ workspaceId, activeSection })
    return <div>Workspace settings content</div>
  },
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
      getLastWorkspaceId: () => mockLastWorkspaceId,
      workspaces: mockWorkspaces,
    }
  },
}))

jest.mock("@/providers/workspace-id", () => ({
  useOptionalWorkspaceId: () => mockContextWorkspaceId,
}))

describe("SettingsModal workspace navigation", () => {
  beforeEach(() => {
    jest.clearAllMocks()
    mockScopes = ["*"]
    mockContextWorkspaceId = "workspace-123"
    mockLastWorkspaceId = undefined
    mockHasGitSync = false
    mockOpen = true
    mockActiveSection = "profile"
    mockWorkspaces = [{ id: "workspace-123", name: "Workspace 123" }]
    mockScopesByWorkspaceId = {}
  })

  it("shows workspace settings sections for the current writable workspace", () => {
    render(<SettingsModal />)

    expect(mockUseUserScopes).toHaveBeenCalledWith("workspace-123", {
      enabled: true,
    })
    expect(screen.getByRole("button", { name: "General" })).toBeInTheDocument()
    expect(
      screen.getByRole("button", { name: "Workflows" })
    ).toBeInTheDocument()
    expect(
      screen.getByRole("button", { name: "AI models" })
    ).toBeInTheDocument()
    expect(screen.getByRole("button", { name: "Files" })).toBeInTheDocument()
    expect(screen.getByRole("button", { name: "Git sync" })).toBeInTheDocument()
  })

  it("hides workspace settings sections without workspace update access", () => {
    mockScopes = ["workspace:read"]

    render(<SettingsModal />)

    expect(mockUseUserScopes).toHaveBeenCalledWith("workspace-123", {
      enabled: true,
    })
    expect(
      screen.queryByRole("button", { name: "General" })
    ).not.toBeInTheDocument()
    expect(
      screen.queryByRole("button", { name: "Workflows" })
    ).not.toBeInTheDocument()
    expect(
      screen.queryByRole("button", { name: "AI models" })
    ).not.toBeInTheDocument()
    expect(
      screen.queryByRole("button", { name: "Files" })
    ).not.toBeInTheDocument()
    expect(
      screen.queryByRole("button", { name: "Git sync" })
    ).not.toBeInTheDocument()
    expect(screen.getByText("Profile content")).toBeInTheDocument()
  })

  it("falls back to profile-only navigation outside a workspace context", () => {
    mockContextWorkspaceId = undefined
    mockWorkspaces = []

    render(<SettingsModal />)

    expect(mockUseUserScopes).toHaveBeenCalledWith(undefined, {
      enabled: false,
    })
    expect(
      screen.queryByRole("button", { name: "General" })
    ).not.toBeInTheDocument()
    expect(
      screen.queryByRole("button", { name: "Workflows" })
    ).not.toBeInTheDocument()
    expect(
      screen.queryByRole("button", { name: "AI models" })
    ).not.toBeInTheDocument()
    expect(
      screen.queryByRole("button", { name: "Files" })
    ).not.toBeInTheDocument()
    expect(
      screen.queryByRole("button", { name: "Git sync" })
    ).not.toBeInTheDocument()
    expect(screen.getByText("Profile content")).toBeInTheDocument()
  })

  it("falls back to the first accessible workspace when the last viewed cookie is stale", async () => {
    mockContextWorkspaceId = undefined
    mockLastWorkspaceId = "missing-workspace"
    mockWorkspaces = [
      { id: "workspace-456", name: "Workspace 456" },
      { id: "workspace-789", name: "Workspace 789" },
    ]

    render(<SettingsModal />)

    await waitFor(() => {
      expect(mockUseUserScopes).toHaveBeenCalledWith("workspace-456", {
        enabled: true,
      })
      expect(mockClearLastWorkspaceId).toHaveBeenCalled()
      expect(
        screen.getByRole("button", { name: "General" })
      ).toBeInTheDocument()
    })
  })

  it("probes additional workspaces until it finds one with update access", async () => {
    mockContextWorkspaceId = undefined
    mockLastWorkspaceId = "workspace-viewer"
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

  it("renders workspace settings content for an active workspace section", () => {
    mockActiveSection = "workspace-general"

    render(<SettingsModal />)

    expect(mockWorkspaceSettingsContainer).toHaveBeenCalledWith({
      workspaceId: "workspace-123",
      activeSection: "workspace-general",
    })
    expect(screen.getByText("Workspace settings content")).toBeInTheDocument()
    expect(screen.queryByText("Profile content")).not.toBeInTheDocument()
  })

  it("does not run workspace or entitlement hooks while closed", () => {
    mockOpen = false

    render(<SettingsModal />)

    expect(mockUseWorkspaceManager).not.toHaveBeenCalled()
    expect(mockUseUserScopes).not.toHaveBeenCalled()
    expect(mockUseEntitlements).not.toHaveBeenCalled()
    expect(mockWorkspaceSettingsContainer).not.toHaveBeenCalled()
  })
})
