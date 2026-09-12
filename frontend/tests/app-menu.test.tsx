/**
 * @jest-environment jsdom
 */

import { fireEvent, render, screen, waitFor } from "@testing-library/react"
import type { InputHTMLAttributes, ReactNode } from "react"
import { AppMenu } from "@/components/sidebar/app-menu"

const mockRouterPush = jest.fn()
const mockUseScopeCheck = jest.fn<boolean | undefined, [string]>()
const mockCreateWorkspace = jest.fn()
const mockReact = jest.requireActual<typeof import("react")>("react")
const mockDialogContext = mockReact.createContext(false)

let mockPathname = "/workspaces/workspace-1/workflows"
let mockScopes: Record<string, boolean | undefined> = {}
let mockWorkspaces: { id: string; name: string }[] = []
let mockEntitlements: Record<string, boolean> = {}
let mockEntitlementsKnown = true
let mockEntitlementsLoading = false

jest.mock("next/link", () => ({
  __esModule: true,
  default: ({ children, ...props }: { children: ReactNode }) => (
    <a {...props}>{children}</a>
  ),
}))

jest.mock("next/navigation", () => ({
  usePathname: () => mockPathname,
  useRouter: () => ({ push: mockRouterPush }),
  useSearchParams: () => new URLSearchParams(),
}))

jest.mock("@/components/auth/scope-guard", () => ({
  useScopeCheck: (scope: string) => mockUseScopeCheck(scope),
}))

jest.mock("@/components/ui/button", () => ({
  Button: ({
    children,
    type,
    disabled,
    onClick,
  }: {
    children: ReactNode
    type?: "button" | "submit" | "reset"
    disabled?: boolean
    onClick?: () => void
  }) => (
    <button type={type ?? "button"} disabled={disabled} onClick={onClick}>
      {children}
    </button>
  ),
}))

jest.mock("@/components/ui/dialog", () => ({
  Dialog: ({ open, children }: { open: boolean; children: ReactNode }) => (
    <mockDialogContext.Provider value={open}>
      {children}
    </mockDialogContext.Provider>
  ),
  DialogClose: ({ children }: { children: ReactNode }) => <>{children}</>,
  DialogContent: ({ children }: { children: ReactNode }) =>
    mockReact.useContext(mockDialogContext) ? (
      <div role="dialog">{children}</div>
    ) : null,
  DialogDescription: ({ children }: { children: ReactNode }) => (
    <p>{children}</p>
  ),
  DialogFooter: ({ children }: { children: ReactNode }) => (
    <div>{children}</div>
  ),
  DialogHeader: ({ children }: { children: ReactNode }) => (
    <div>{children}</div>
  ),
  DialogTitle: ({ children }: { children: ReactNode }) => <h2>{children}</h2>,
  DialogTrigger: ({ children }: { children: ReactNode }) => <>{children}</>,
}))

jest.mock("@/components/ui/dropdown-menu", () => ({
  DropdownMenu: ({ children }: { children: ReactNode }) => (
    <div>{children}</div>
  ),
  DropdownMenuContent: ({ children }: { children: ReactNode }) => (
    <div>{children}</div>
  ),
  DropdownMenuItem: ({
    asChild,
    children,
    onSelect,
  }: {
    asChild?: boolean
    children: ReactNode
    onSelect?: (event: { preventDefault: () => void }) => void
  }) => {
    if (asChild) {
      return <>{children}</>
    }

    return (
      <button
        type="button"
        onClick={() => onSelect?.({ preventDefault: jest.fn() })}
      >
        {children}
      </button>
    )
  },
  DropdownMenuLabel: ({ children }: { children: ReactNode }) => (
    <div>{children}</div>
  ),
  DropdownMenuSeparator: () => <hr />,
  DropdownMenuTrigger: ({ children }: { children: ReactNode }) => (
    <>{children}</>
  ),
}))

jest.mock("@/components/ui/input", () => ({
  Input: (props: InputHTMLAttributes<HTMLInputElement>) => <input {...props} />,
}))

jest.mock("@/components/ui/label", () => ({
  Label: ({ children, ...props }: { children: ReactNode }) => (
    <label {...props}>{children}</label>
  ),
}))

jest.mock("@/components/ui/sidebar", () => ({
  SidebarMenu: ({ children }: { children: ReactNode }) => <ul>{children}</ul>,
  SidebarMenuButton: ({
    asChild,
    children,
  }: {
    asChild?: boolean
    children: ReactNode
  }) => (asChild ? <>{children}</> : <button type="button">{children}</button>),
  SidebarMenuItem: ({ children }: { children: ReactNode }) => (
    <li>{children}</li>
  ),
}))

jest.mock("@/hooks/use-entitlements", () => ({
  useEntitlements: () => ({
    hasEntitlement: (key: string) => mockEntitlements[key] ?? false,
    hasEntitlementData: mockEntitlementsKnown,
    isLoading: mockEntitlementsLoading,
  }),
}))

jest.mock("@/hooks/use-organization", () => ({
  useOrganization: () => ({ organization: { id: "org-1" } }),
  useOrganizationMemberships: () => ({ organizations: [] }),
}))

jest.mock("@/lib/hooks", () => ({
  useWorkspaceManager: () => ({
    workspaces: mockWorkspaces,
    createWorkspace: mockCreateWorkspace,
  }),
}))

function renderAppMenu() {
  return render(<AppMenu workspaceId="workspace-1" />)
}

describe("AppMenu workspace creation", () => {
  beforeEach(() => {
    jest.clearAllMocks()
    mockPathname = "/workspaces/workspace-1/workflows"
    mockScopes = {
      "org:update": false,
      "workspace:create": true,
    }
    mockWorkspaces = [
      { id: "workspace-1", name: "First workspace" },
      { id: "workspace-2", name: "Second workspace" },
    ]
    mockEntitlements = { multi_workspace: true }
    mockEntitlementsKnown = true
    mockEntitlementsLoading = false
    mockCreateWorkspace.mockResolvedValue({
      id: "workspace-3",
      name: "Third workspace",
    })
    mockUseScopeCheck.mockImplementation((scope) => mockScopes[scope] ?? false)
  })

  it("shows Add workspace only for entitled users with create scope", () => {
    const { rerender } = renderAppMenu()

    expect(
      screen.getByRole("button", { name: "Add workspace" })
    ).toBeInTheDocument()

    mockEntitlements = { multi_workspace: false }
    rerender(<AppMenu workspaceId="workspace-1" />)

    expect(
      screen.queryByRole("button", { name: "Add workspace" })
    ).not.toBeInTheDocument()

    mockEntitlements = { multi_workspace: true }
    mockScopes["workspace:create"] = false
    rerender(<AppMenu workspaceId="workspace-1" />)
    expect(
      screen.queryByRole("button", { name: "Add workspace" })
    ).not.toBeInTheDocument()
  })

  it("keeps all existing workspace links when creation is unavailable", () => {
    mockEntitlements = { multi_workspace: false }

    renderAppMenu()

    expect(
      screen.getByRole("link", { name: /First workspace$/ })
    ).toHaveAttribute("href", "/workspaces/workspace-1/workflows")
    expect(
      screen.getByRole("link", { name: /Second workspace$/ })
    ).toHaveAttribute("href", "/workspaces/workspace-2/workflows")
  })

  it.each([
    { loading: true, known: false },
    { loading: false, known: false },
  ])(
    "keeps creation neutral while entitlement data is unavailable",
    ({ loading, known }) => {
      mockEntitlements = {}
      mockEntitlementsLoading = loading
      mockEntitlementsKnown = known

      renderAppMenu()

      expect(
        screen.queryByRole("button", { name: "Add workspace" })
      ).not.toBeInTheDocument()
      expect(screen.queryByText(/upgrade/i)).not.toBeInTheDocument()
      expect(
        screen.getByRole("link", { name: /Second workspace$/ })
      ).toBeInTheDocument()
    }
  )

  it("creates and navigates to a new workspace for entitled users", async () => {
    renderAppMenu()

    fireEvent.click(screen.getByRole("button", { name: "Add workspace" }))
    fireEvent.change(screen.getByLabelText("Workspace name"), {
      target: { value: "Third workspace" },
    })
    fireEvent.click(screen.getByRole("button", { name: "Create workspace" }))

    await waitFor(() => {
      expect(mockCreateWorkspace).toHaveBeenCalledWith({
        name: "Third workspace",
      })
    })
    expect(mockRouterPush).toHaveBeenCalledWith("/workspaces/workspace-3/chat")
  })

  it("removes the open dialog if entitlement disappears before submit", () => {
    const { rerender } = renderAppMenu()

    fireEvent.click(screen.getByRole("button", { name: "Add workspace" }))
    expect(
      screen.getByRole("heading", { name: "Create a new workspace" })
    ).toBeInTheDocument()

    mockEntitlements = { multi_workspace: false }
    rerender(<AppMenu workspaceId="workspace-1" />)

    expect(
      screen.queryByRole("heading", { name: "Create a new workspace" })
    ).not.toBeInTheDocument()
    expect(mockCreateWorkspace).not.toHaveBeenCalled()
  })
})
