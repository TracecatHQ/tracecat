/**
 * @jest-environment jsdom
 */

import { render, screen, waitFor } from "@testing-library/react"
import userEvent from "@testing-library/user-event"
import type { RoleReadWithScopes, WorkspaceRead } from "@/client"
import { InviteMemberDialog } from "@/components/organization/invite-member-dialog"
import { useOrgMembers, useRbacRoles, useWorkspaceManager } from "@/lib/hooks"

jest.mock("@/lib/hooks", () => ({
  useOrgMembers: jest.fn(),
  useRbacRoles: jest.fn(),
  useWorkspaceManager: jest.fn(),
}))

jest.mock("@/components/auth/scope-guard", () => ({
  useScopeCheck: () => true,
}))

jest.mock("@/components/ui/dialog", () => ({
  Dialog: ({ children }: { children: React.ReactNode }) => (
    <div>{children}</div>
  ),
  DialogContent: ({ children }: { children: React.ReactNode }) => (
    <div>{children}</div>
  ),
  DialogDescription: ({ children }: { children: React.ReactNode }) => (
    <div>{children}</div>
  ),
  DialogFooter: ({ children }: { children: React.ReactNode }) => (
    <div>{children}</div>
  ),
  DialogHeader: ({ children }: { children: React.ReactNode }) => (
    <div>{children}</div>
  ),
  DialogTitle: ({ children }: { children: React.ReactNode }) => (
    <h2>{children}</h2>
  ),
}))

// Radix Select needs pointer events jsdom lacks. Render a native select whose
// options are collected from the sibling SelectContent subtree via context.
jest.mock("@/components/ui/select", () => {
  const React: typeof import("react") = require("react")

  type SelectContextValue = {
    value?: string
    onValueChange?: (value: string) => void
    options: string[]
    register: (value: string) => void
  }

  const SelectContext = React.createContext<SelectContextValue>({
    options: [],
    register: () => {},
  })

  function Select({
    children,
    value,
    onValueChange,
  }: {
    children: React.ReactNode
    value?: string
    onValueChange?: (value: string) => void
  }) {
    const [options, setOptions] = React.useState<string[]>([])
    const register = React.useCallback((value: string) => {
      setOptions((current) =>
        current.includes(value) ? current : [...current, value]
      )
    }, [])
    const context = React.useMemo(
      () => ({ value, onValueChange, options, register }),
      [value, onValueChange, options, register]
    )
    return (
      <SelectContext.Provider value={context}>
        {children}
      </SelectContext.Provider>
    )
  }

  function SelectTrigger({
    "aria-label": ariaLabel,
  }: {
    children?: React.ReactNode
    "aria-label"?: string
  }) {
    const { value, onValueChange, options } = React.useContext(SelectContext)
    return (
      <select
        aria-label={ariaLabel}
        value={value ?? ""}
        onChange={(event: React.ChangeEvent<HTMLSelectElement>) =>
          onValueChange?.(event.target.value)
        }
      >
        <option value="" />
        {options.map((option) => (
          <option key={option} value={option} />
        ))}
      </select>
    )
  }

  function SelectItem({
    value,
  }: {
    children?: React.ReactNode
    value: string
  }) {
    const { register } = React.useContext(SelectContext)
    React.useEffect(() => {
      register(value)
    }, [register, value])
    return null
  }

  return {
    Select,
    SelectTrigger,
    SelectContent: ({ children }: { children: React.ReactNode }) => (
      <>{children}</>
    ),
    SelectItem,
    SelectValue: () => null,
  }
})

const ORG_ADMIN_ROLE_ID = "11111111-1111-1111-1111-111111111111"
const ORG_MEMBER_ROLE_ID = "22222222-2222-2222-2222-222222222222"
const WORKSPACE_EDITOR_ROLE_ID = "33333333-3333-3333-3333-333333333333"

function createRole(
  id: string,
  name: string,
  slug: string
): RoleReadWithScopes {
  return {
    id,
    name,
    slug,
    description: null,
    organization_id: "org-1",
    created_at: "2026-01-01T00:00:00Z",
    updated_at: "2026-01-01T00:00:00Z",
    created_by: null,
    scopes: [],
    is_system: true,
  }
}

const ROLES = [
  createRole(ORG_ADMIN_ROLE_ID, "Admin", "organization-admin"),
  createRole(ORG_MEMBER_ROLE_ID, "Member", "organization-member"),
  createRole(WORKSPACE_EDITOR_ROLE_ID, "Editor", "workspace-editor"),
]

const WORKSPACES = [
  { id: "ws-a", name: "Workspace A" },
  { id: "ws-b", name: "Workspace B" },
] as WorkspaceRead[]

const mockUseOrgMembers = useOrgMembers as jest.MockedFunction<
  typeof useOrgMembers
>
const mockUseRbacRoles = useRbacRoles as jest.MockedFunction<
  typeof useRbacRoles
>
const mockUseWorkspaceManager = useWorkspaceManager as jest.MockedFunction<
  typeof useWorkspaceManager
>

let createInvitation: jest.Mock

beforeEach(() => {
  createInvitation = jest.fn().mockResolvedValue(undefined)
  mockUseOrgMembers.mockReturnValue({
    createInvitation,
    createInvitationIsPending: false,
  } as unknown as ReturnType<typeof useOrgMembers>)
  mockUseRbacRoles.mockReturnValue({ roles: ROLES } as unknown as ReturnType<
    typeof useRbacRoles
  >)
  mockUseWorkspaceManager.mockReturnValue({
    workspaces: WORKSPACES,
  } as unknown as ReturnType<typeof useWorkspaceManager>)
})

afterEach(() => {
  jest.clearAllMocks()
})

describe("InviteMemberDialog", () => {
  it("omits the baseline organization-member role from the org role picker", () => {
    render(<InviteMemberDialog open={true} onOpenChange={() => {}} />)

    const rolePicker = screen.getByLabelText("Grant 1 role")
    const optionValues = Array.from(rolePicker.querySelectorAll("option")).map(
      (option) => option.getAttribute("value")
    )

    expect(optionValues).toContain(ORG_ADMIN_ROLE_ID)
    expect(optionValues).not.toContain(ORG_MEMBER_ROLE_ID)
  })

  it("submits an org-wide grant and a workspace grant in the request shape", async () => {
    const user = userEvent.setup()
    render(<InviteMemberDialog open={true} onOpenChange={() => {}} />)

    await user.type(screen.getByPlaceholderText("user@example.com"), "a@b.com")
    await user.selectOptions(
      screen.getByLabelText("Grant 1 role"),
      ORG_ADMIN_ROLE_ID
    )

    await user.click(screen.getByRole("button", { name: /Add grant/ }))
    await user.selectOptions(screen.getByLabelText("Grant 2 scope"), "ws-a")
    await user.selectOptions(
      screen.getByLabelText("Grant 2 role"),
      WORKSPACE_EDITOR_ROLE_ID
    )

    await user.click(screen.getByRole("button", { name: "Send invitation" }))

    await waitFor(() => {
      expect(createInvitation).toHaveBeenCalledWith({
        email: "a@b.com",
        grants: [
          { role_id: ORG_ADMIN_ROLE_ID, workspace_id: null },
          { role_id: WORKSPACE_EDITOR_ROLE_ID, workspace_id: "ws-a" },
        ],
      })
    })
  })

  it("blocks submission when a grant row has no role selected", async () => {
    const user = userEvent.setup()
    render(<InviteMemberDialog open={true} onOpenChange={() => {}} />)

    await user.type(screen.getByPlaceholderText("user@example.com"), "a@b.com")
    await user.click(screen.getByRole("button", { name: "Send invitation" }))

    await waitFor(() => {
      expect(screen.getByText("Select a role")).toBeInTheDocument()
    })
    expect(createInvitation).not.toHaveBeenCalled()
  })

  it("blocks two grants on the same scope", async () => {
    const user = userEvent.setup()
    render(<InviteMemberDialog open={true} onOpenChange={() => {}} />)

    await user.type(screen.getByPlaceholderText("user@example.com"), "a@b.com")
    await user.selectOptions(
      screen.getByLabelText("Grant 1 role"),
      ORG_ADMIN_ROLE_ID
    )
    await user.click(screen.getByRole("button", { name: /Add grant/ }))
    await user.selectOptions(screen.getByLabelText("Grant 2 scope"), "org-wide")
    await user.selectOptions(
      screen.getByLabelText("Grant 2 role"),
      ORG_ADMIN_ROLE_ID
    )
    await user.click(screen.getByRole("button", { name: "Send invitation" }))

    await waitFor(() => {
      expect(
        screen.getByText("This scope already has a grant")
      ).toBeInTheDocument()
    })
    expect(createInvitation).not.toHaveBeenCalled()
  })

  it("runs no data hooks while closed", () => {
    render(<InviteMemberDialog open={false} onOpenChange={() => {}} />)

    expect(mockUseOrgMembers).not.toHaveBeenCalled()
    expect(mockUseRbacRoles).not.toHaveBeenCalled()
    expect(mockUseWorkspaceManager).not.toHaveBeenCalled()
  })

  it("pre-fills the first grant with the handoff workspace", () => {
    render(
      <InviteMemberDialog
        open={true}
        onOpenChange={() => {}}
        initialWorkspaceId="ws-b"
      />
    )

    expect(screen.getByLabelText("Grant 1 scope")).toHaveValue("ws-b")
    const optionValues = Array.from(
      screen.getByLabelText("Grant 1 role").querySelectorAll("option")
    ).map((option) => option.getAttribute("value"))
    expect(optionValues).toContain(WORKSPACE_EDITOR_ROLE_ID)
    expect(optionValues).not.toContain(ORG_MEMBER_ROLE_ID)
  })
})
