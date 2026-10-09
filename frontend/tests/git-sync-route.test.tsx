/**
 * @jest-environment jsdom
 */

import { render, screen } from "@testing-library/react"
import WorkspaceGitSyncPage from "@/app/workspaces/[workspaceId]/git-sync/page"

let mockEntitlements = {
  hasEntitlement: (_key: string) => false,
  hasEntitlementData: true,
  isLoading: false,
}

jest.mock("@/hooks/use-entitlements", () => ({
  useEntitlements: () => mockEntitlements,
}))

jest.mock("@/hooks/use-workspace", () => ({
  useWorkspaceDetails: () => ({
    workspace: { id: "workspace-1", name: "Workspace 1", settings: {} },
    workspaceLoading: false,
    workspaceError: null,
  }),
}))

jest.mock("@/components/auth/scope-guard", () => ({
  useScopeCheck: () => true,
}))

jest.mock("@/components/workspace-sync/git-sync-view", () => ({
  GitSyncHeader: () => <header>Git Sync</header>,
  GitSyncView: () => <div>Git Sync view</div>,
}))

describe("WorkspaceGitSyncPage", () => {
  it("asks to reload when the plan can't be checked, not to upgrade", () => {
    mockEntitlements = {
      hasEntitlement: () => false,
      hasEntitlementData: false,
      isLoading: false,
    }

    render(<WorkspaceGitSyncPage />)

    expect(screen.getByText("Unable to check plan access")).toBeInTheDocument()
    expect(screen.getByRole("button", { name: "Reload" })).toBeInTheDocument()
    expect(screen.queryByText("Upgrade required")).not.toBeInTheDocument()
  })

  it("shows the upgrade state when the plan lacks Git Sync", () => {
    mockEntitlements = {
      hasEntitlement: () => false,
      hasEntitlementData: true,
      isLoading: false,
    }

    render(<WorkspaceGitSyncPage />)

    expect(screen.getByText("Upgrade required")).toBeInTheDocument()
  })

  it("shows the page when the plan includes Git Sync", () => {
    mockEntitlements = {
      hasEntitlement: () => true,
      hasEntitlementData: true,
      isLoading: false,
    }

    render(<WorkspaceGitSyncPage />)

    expect(screen.getByText("Git Sync view")).toBeInTheDocument()
  })
})
