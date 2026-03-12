/**
 * @jest-environment jsdom
 */

import { render, screen } from "@testing-library/react"
import type { ReactNode } from "react"
import { SettingsModal } from "@/components/settings/settings-modal"

const mockSetOpen = jest.fn()
const mockSetActiveSection = jest.fn()
const mockLogout = jest.fn()

let mockScopes = ["*"]
let mockWorkspaceId: string | undefined = "workspace-123"
let mockHasGitSync = false

jest.mock("@/components/settings/settings-modal-context", () => ({
  useSettingsModal: () => ({
    open: true,
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
  useEntitlements: () => ({
    hasEntitlement: (entitlement: string) =>
      mockHasGitSync && entitlement === "git_sync",
  }),
}))

jest.mock("@/lib/hooks", () => ({
  useUserScopes: () => ({
    userScopes: { scopes: mockScopes },
    isLoading: false,
  }),
  useWorkspaceManager: () => ({
    getLastWorkspaceId: () => mockWorkspaceId,
  }),
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
})
