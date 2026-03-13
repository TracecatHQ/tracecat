/**
 * @jest-environment jsdom
 */

import { render, screen } from "@testing-library/react"
import type { ReactNode } from "react"
import { SettingsModal } from "@/components/settings/settings-modal"
import type { SettingsSection } from "@/components/settings/settings-modal-context"

const mockSetOpen = jest.fn()
const mockSetActiveSection = jest.fn()
const mockLogout = jest.fn()
const mockUseEntitlements = jest.fn()
const mockUseUserScopes = jest.fn()
const mockWorkspaceSettingsContainer = jest.fn()

let mockScopes = ["*"]
let mockWorkspaceId: string | undefined = "workspace-123"
let mockHasGitSync = false
let mockOpen = true
let mockActiveSection: SettingsSection = "profile"
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
}))

jest.mock("@/providers/workspace-id", () => ({
  useOptionalWorkspaceId: () => mockWorkspaceId,
}))

describe("SettingsModal workspace navigation", () => {
  beforeEach(() => {
    jest.clearAllMocks()
    mockScopes = ["*"]
    mockWorkspaceId = "workspace-123"
    mockHasGitSync = false
    mockOpen = true
    mockActiveSection = "profile"
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
      screen.queryByRole("button", { name: "Files" })
    ).not.toBeInTheDocument()
    expect(
      screen.queryByRole("button", { name: "Git sync" })
    ).not.toBeInTheDocument()
    expect(screen.getByText("Profile content")).toBeInTheDocument()
  })

  it("falls back to profile-only navigation outside a workspace context", () => {
    mockWorkspaceId = undefined

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
      screen.queryByRole("button", { name: "Files" })
    ).not.toBeInTheDocument()
    expect(
      screen.queryByRole("button", { name: "Git sync" })
    ).not.toBeInTheDocument()
    expect(screen.getByText("Profile content")).toBeInTheDocument()
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

    expect(mockUseUserScopes).not.toHaveBeenCalled()
    expect(mockUseEntitlements).not.toHaveBeenCalled()
    expect(mockWorkspaceSettingsContainer).not.toHaveBeenCalled()
  })
})
