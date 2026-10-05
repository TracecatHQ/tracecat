/**
 * @jest-environment jsdom
 */

import { render, screen, waitFor, within } from "@testing-library/react"
import userEvent from "@testing-library/user-event"
import type {
  GroupReadWithMembers,
  RoleReadWithScopes,
  WorkspaceRead,
} from "@/client"
import { useScopeCheck } from "@/components/auth/scope-guard"
import { InviteMemberDialog } from "@/components/organization/invite-member-dialog"
import { useEntitlements } from "@/hooks/use-entitlements"
import {
  useOrgMembers,
  useRbacGroups,
  useRbacRoles,
  useWorkspaceManager,
} from "@/lib/hooks"

jest.mock("@/lib/hooks", () => ({
  useOrgMembers: jest.fn(),
  useRbacGroups: jest.fn(),
  useRbacRoles: jest.fn(),
  useWorkspaceManager: jest.fn(),
}))

jest.mock("@/hooks/use-entitlements", () => ({
  useEntitlements: jest.fn(),
}))

jest.mock("@/components/auth/scope-guard", () => ({
  useScopeCheck: jest.fn(),
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
const WORKSPACE_EDITOR_ROLE_ID = "33333333-3333-3333-3333-333333333333"
const CUSTOM_ROLE_ID = "44444444-4444-4444-4444-444444444444"

function createRole(
  id: string,
  name: string,
  slug: string | null
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
    // The picker reads the role level from org:* scopes, not the slug.
    scopes: slug?.startsWith("organization-")
      ? [
          {
            id: "scope-org-read",
            name: "org:read",
            resource: "org",
            action: "read",
            source: "platform",
            created_at: "2026-01-01T00:00:00Z",
            updated_at: "2026-01-01T00:00:00Z",
          },
        ]
      : [],
    is_system: true,
  }
}

const ROLES = [
  createRole(ORG_ADMIN_ROLE_ID, "Admin", "organization-admin"),
  createRole(WORKSPACE_EDITOR_ROLE_ID, "Editor", "workspace-editor"),
  createRole(CUSTOM_ROLE_ID, "Custom", null),
]

const WORKSPACES = [
  { id: "ws-a", name: "Workspace A" },
  { id: "ws-b", name: "Workspace B" },
] as WorkspaceRead[]

const GROUPS = [
  { id: "group-sec", name: "Security", is_idp_managed: false },
  { id: "group-okta", name: "Okta admins", is_idp_managed: true },
  { id: "group-eng", name: "Engineering", is_idp_managed: false },
] as GroupReadWithMembers[]

const mockUseScopeCheck = useScopeCheck as jest.MockedFunction<
  typeof useScopeCheck
>
const mockUseEntitlements = useEntitlements as jest.MockedFunction<
  typeof useEntitlements
>
const mockUseRbacGroups = useRbacGroups as jest.MockedFunction<
  typeof useRbacGroups
>
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
  mockUseRbacGroups.mockReturnValue({ groups: GROUPS } as unknown as ReturnType<
    typeof useRbacGroups
  >)
  mockUseScopeCheck.mockReturnValue(true)
  mockEntitlements(true)
})

function mockEntitlements(rbacAddons: boolean) {
  mockUseEntitlements.mockReturnValue({
    hasEntitlement: (key) => key === "rbac_addons" && rbacAddons,
    hasEntitlementData: true,
    isLoading: false,
  })
}

async function fillRequiredFields(user: ReturnType<typeof userEvent.setup>) {
  await user.type(screen.getByPlaceholderText("user@example.com"), "a@b.com")
  await user.selectOptions(
    screen.getByLabelText("Grant 1 role"),
    ORG_ADMIN_ROLE_ID
  )
}

afterEach(() => {
  jest.clearAllMocks()
})

describe("InviteMemberDialog", () => {
  it("offers org roles only org-wide and workspace roles on every scope", async () => {
    const user = userEvent.setup()
    render(<InviteMemberDialog open={true} onOpenChange={() => {}} />)

    const optionsFor = (label: string) =>
      Array.from(screen.getByLabelText(label).querySelectorAll("option")).map(
        (option) => option.getAttribute("value")
      )

    // Grant 1 defaults to org-wide: a workspace role there applies in every
    // workspace.
    expect(optionsFor("Grant 1 role")).toContain(ORG_ADMIN_ROLE_ID)
    expect(optionsFor("Grant 1 role")).toContain(WORKSPACE_EDITOR_ROLE_ID)
    expect(optionsFor("Grant 1 role")).toContain(CUSTOM_ROLE_ID)

    await user.click(screen.getByRole("button", { name: /Add grant/ }))
    await user.selectOptions(screen.getByLabelText("Grant 2 scope"), "ws-a")

    expect(optionsFor("Grant 2 role")).toContain(WORKSPACE_EDITOR_ROLE_ID)
    expect(optionsFor("Grant 2 role")).not.toContain(ORG_ADMIN_ROLE_ID)
    expect(optionsFor("Grant 2 role")).toContain(CUSTOM_ROLE_ID)
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
    expect(mockUseRbacGroups).not.toHaveBeenCalled()
    expect(mockUseEntitlements).not.toHaveBeenCalled()
  })

  it("hides the groups section without the rbac_addons entitlement", () => {
    mockEntitlements(false)
    render(<InviteMemberDialog open={true} onOpenChange={() => {}} />)

    expect(screen.queryByText("Groups")).not.toBeInTheDocument()
    expect(mockUseRbacGroups).not.toHaveBeenCalled()
  })

  it("hides the groups section while entitlements are unknown", () => {
    mockUseEntitlements.mockReturnValue({
      hasEntitlement: () => false,
      hasEntitlementData: false,
      isLoading: true,
    })
    render(<InviteMemberDialog open={true} onOpenChange={() => {}} />)

    expect(screen.queryByText("Groups")).not.toBeInTheDocument()
    expect(mockUseRbacGroups).not.toHaveBeenCalled()
  })

  it("hides the groups section without org:rbac:update", () => {
    mockUseScopeCheck.mockImplementation((scope) => scope !== "org:rbac:update")
    render(<InviteMemberDialog open={true} onOpenChange={() => {}} />)

    expect(screen.queryByText("Groups")).not.toBeInTheDocument()
    expect(mockUseRbacGroups).not.toHaveBeenCalled()
  })

  it("offers groups sorted by name, leaving out IdP-managed ones", async () => {
    const user = userEvent.setup()
    render(<InviteMemberDialog open={true} onOpenChange={() => {}} />)

    const trigger = screen.getByRole("combobox", { name: "Groups" })
    expect(trigger).toHaveTextContent("Select groups")
    await user.click(trigger)

    expect(
      within(screen.getByRole("listbox"))
        .getAllByRole("option")
        .map((option) => option.textContent)
    ).toEqual(["Engineering", "Security"])
  })

  it("sends group_ids for the selected groups", async () => {
    const user = userEvent.setup()
    render(<InviteMemberDialog open={true} onOpenChange={() => {}} />)

    await fillRequiredFields(user)
    const trigger = screen.getByRole("combobox", { name: "Groups" })
    await user.click(trigger)
    await user.click(screen.getByRole("option", { name: "Engineering" }))
    await user.click(screen.getByRole("option", { name: "Security" }))
    expect(trigger).toHaveTextContent("EngineeringSecurity")

    await user.click(screen.getByRole("button", { name: "Send invitation" }))

    await waitFor(() => {
      expect(createInvitation).toHaveBeenCalledWith({
        email: "a@b.com",
        grants: [{ role_id: ORG_ADMIN_ROLE_ID, workspace_id: null }],
        group_ids: ["group-eng", "group-sec"],
      })
    })
  })
})
