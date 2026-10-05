import { render, screen, within } from "@testing-library/react"
import userEvent from "@testing-library/user-event"
import type {
  GroupReadWithMembers,
  GroupRoleAssignmentReadWithDetails,
} from "@/client"
import { useScopeCheck } from "@/components/auth/scope-guard"
import { OrgRbacGroups } from "@/components/organization/org-rbac-groups"
import {
  useOrgMembers,
  useRbacAssignments,
  useRbacGroup,
  useRbacGroups,
  useRbacRoles,
  useWorkspaceManager,
} from "@/lib/hooks"

jest.mock("@/components/auth/scope-guard", () => ({ useScopeCheck: jest.fn() }))
jest.mock("@/lib/hooks", () => ({
  useOrgMembers: jest.fn(),
  useRbacAssignments: jest.fn(),
  useRbacGroup: jest.fn(),
  useRbacGroups: jest.fn(),
  useRbacRoles: jest.fn(),
  useWorkspaceManager: jest.fn(),
}))

function buildGroup(
  overrides: Partial<GroupReadWithMembers> & { id: string; name: string }
): GroupReadWithMembers {
  return {
    organization_id: "org-1",
    created_at: "2026-01-01",
    updated_at: "2026-01-01",
    member_count: 2,
    is_idp_managed: false,
    members: [
      { user_id: "user-1", email: "one@example.com", added_at: "2026-01-01" },
      { user_id: "user-2", email: "two@example.com", added_at: "2026-01-01" },
    ],
    ...overrides,
  }
}

const localGroup = buildGroup({
  id: "group-1",
  name: "Operators",
  description: "Handles on-call rotations",
})
const idpGroup = buildGroup({
  id: "group-2",
  name: "Directory team",
  is_idp_managed: true,
})
const groups = [localGroup, idpGroup]
const assignments: GroupRoleAssignmentReadWithDetails[] = [
  {
    id: "assignment-1",
    organization_id: "org-1",
    group_id: "group-1",
    group_name: "Operators",
    role_id: "role-1",
    role_name: "Workspace Editor",
    workspace_id: null,
    workspace_name: null,
    assigned_at: "2026-01-01",
  },
]

function getRow(name: string): HTMLElement {
  const row = screen.getByText(name).closest<HTMLElement>(".group")
  if (!row) {
    throw new Error(`Row not found: ${name}`)
  }
  return row
}

async function openMenu(user: ReturnType<typeof userEvent.setup>, row: string) {
  await user.click(
    within(getRow(row)).getByRole("button", { name: "Open menu" })
  )
}

function renderGroups() {
  render(<OrgRbacGroups />)
  return userEvent.setup()
}

beforeEach(() => {
  jest.clearAllMocks()
  jest.mocked(useScopeCheck).mockReturnValue(true)
  jest.mocked(useRbacGroups).mockReturnValue({
    groups,
    isLoading: false,
    error: null,
    addGroupMember: jest.fn(),
    removeGroupMember: jest.fn(),
  } as unknown as ReturnType<typeof useRbacGroups>)
  jest.mocked(useRbacGroup).mockImplementation(
    (groupId: string) =>
      ({
        group: groups.find((group) => group.id === groupId),
        isLoading: false,
        error: null,
      }) as ReturnType<typeof useRbacGroup>
  )
  jest.mocked(useRbacAssignments).mockReturnValue({
    assignments,
    createAssignment: jest.fn(),
    deleteAssignment: jest.fn(),
  } as unknown as ReturnType<typeof useRbacAssignments>)
  jest.mocked(useOrgMembers).mockReturnValue({
    orgMembers: [],
  } as unknown as ReturnType<typeof useOrgMembers>)
  jest.mocked(useRbacRoles).mockReturnValue({
    roles: [],
  } as unknown as ReturnType<typeof useRbacRoles>)
  jest.mocked(useWorkspaceManager).mockReturnValue({
    workspaces: [],
  } as unknown as ReturnType<typeof useWorkspaceManager>)
})

describe("OrgRbacGroups", () => {
  it("offers only copy, edit, and delete in the row menu", async () => {
    const user = renderGroups()
    await openMenu(user, "Operators")

    expect(
      screen.getAllByRole("menuitem").map((item) => item.textContent)
    ).toEqual(["Copy group ID", "Edit group", "Delete group"])
    expect(
      screen.queryByRole("menuitem", { name: "Update members" })
    ).not.toBeInTheDocument()
    expect(
      screen.queryByRole("menuitem", { name: "Update roles" })
    ).not.toBeInTheDocument()
  })

  it("shows only badges in the row header", () => {
    renderGroups()

    expect(within(getRow("Operators")).getAllByText(/2 members/)).toHaveLength(
      1
    )
    expect(
      within(getRow("Directory team")).getAllByText(/2 members/)
    ).toHaveLength(1)
    expect(
      screen.queryByText("Handles on-call rotations")
    ).not.toBeInTheDocument()
  })

  it("does not fetch the group while the row is collapsed", () => {
    renderGroups()

    expect(useRbacGroup).not.toHaveBeenCalled()
    expect(useOrgMembers).not.toHaveBeenCalled()
  })

  it("expands with both sections open", async () => {
    const user = renderGroups()
    await user.click(screen.getByText("Operators"))

    expect(screen.getByRole("button", { name: "Members (2)" })).toHaveAttribute(
      "aria-expanded",
      "true"
    )
    expect(screen.getByRole("button", { name: "Roles (1)" })).toHaveAttribute(
      "aria-expanded",
      "true"
    )
    expect(useRbacGroup).toHaveBeenCalledWith("group-1")
    expect(screen.getByText("one@example.com")).toBeInTheDocument()
    expect(screen.getByText("Workspace Editor")).toBeInTheDocument()
    expect(
      screen.getByRole("button", { name: "Add member" })
    ).toBeInTheDocument()
    expect(
      screen.getAllByRole("button", { name: "Remove member" })
    ).toHaveLength(2)
    expect(screen.getByRole("button", { name: "Add role" })).toBeInTheDocument()
    expect(
      screen.getByRole("button", { name: "Remove role" })
    ).toBeInTheDocument()
    expect(
      screen.queryByText(/All members of this group will inherit/)
    ).not.toBeInTheDocument()
  })

  it("keeps the sections individually collapsible", async () => {
    const user = renderGroups()
    await user.click(screen.getByText("Operators"))
    await user.click(screen.getByRole("button", { name: "Members (2)" }))

    expect(screen.queryByText("one@example.com")).not.toBeInTheDocument()
    expect(screen.getByText("Workspace Editor")).toBeInTheDocument()
  })

  it("hides member controls for an IdP-managed group", async () => {
    const user = renderGroups()
    await user.click(screen.getByText("Directory team"))

    expect(screen.getByText("one@example.com")).toBeInTheDocument()
    expect(
      screen.getByText(/Membership is managed by your identity provider/)
    ).toBeInTheDocument()
    expect(
      screen.queryByRole("button", { name: "Add member" })
    ).not.toBeInTheDocument()
    expect(
      screen.queryByRole("button", { name: "Remove member" })
    ).not.toBeInTheDocument()
    expect(useOrgMembers).not.toHaveBeenCalled()
  })

  it("shows the empty roles state", async () => {
    const user = renderGroups()
    await user.click(screen.getByText("Directory team"))

    expect(screen.getByText("No roles")).toBeInTheDocument()
  })
})
