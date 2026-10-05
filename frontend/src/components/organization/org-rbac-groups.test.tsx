import { render, screen, within } from "@testing-library/react"
import userEvent from "@testing-library/user-event"
import type {
  GroupReadWithMembers,
  GroupRoleAssignmentReadWithDetails,
  OrgMemberRead,
} from "@/client"
import { useScopeCheck } from "@/components/auth/scope-guard"
import { OrgRbacGroups } from "@/components/organization/org-rbac-groups"
import { TooltipProvider } from "@/components/ui/tooltip"
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

function buildMember(
  overrides: Partial<OrgMemberRead> & { email: string }
): OrgMemberRead {
  return { role_name: "Member", status: "active", ...overrides }
}

const orgMembers: OrgMemberRead[] = [
  buildMember({ user_id: "user-1", email: "one@example.com" }),
  buildMember({
    user_id: "user-3",
    email: "three@example.com",
    first_name: "Three",
    role_slug: "organization-admin",
    roles: [{ id: "role-1", name: "Workspace Editor" }],
  }),
  buildMember({ user_id: "user-4", email: "four@example.com" }),
  buildMember({
    invitation_id: "invite-1",
    email: "invited@example.com",
    status: "invited",
  }),
]
const addGroupMembers = jest.fn()

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
  render(
    <TooltipProvider>
      <OrgRbacGroups />
    </TooltipProvider>
  )
  return userEvent.setup()
}

async function openAddMembersDialog(user: ReturnType<typeof userEvent.setup>) {
  await user.click(screen.getByText("Operators"))
  await user.click(screen.getByRole("button", { name: "Add members" }))
  return screen.getByRole("dialog")
}

beforeEach(() => {
  jest.clearAllMocks()
  jest.mocked(useScopeCheck).mockReturnValue(true)
  jest.mocked(useRbacGroups).mockReturnValue({
    groups,
    isLoading: false,
    error: null,
    addGroupMembers,
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
  addGroupMembers.mockResolvedValue({ failedUserIds: [] })
  jest.mocked(useOrgMembers).mockReturnValue({
    orgMembers,
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
      screen.getByRole("button", { name: "Add members" })
    ).toBeInTheDocument()
    expect(
      screen.queryByRole("combobox", { name: "User" })
    ).not.toBeInTheDocument()
    expect(screen.queryByText("Select a user")).not.toBeInTheDocument()
    expect(
      screen.getAllByRole("button", { name: "Remove member" })
    ).toHaveLength(2)
    expect(useOrgMembers).not.toHaveBeenCalled()
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
      screen.queryByRole("button", { name: "Add members" })
    ).not.toBeInTheDocument()
    expect(
      screen.queryByRole("button", { name: "Remove member" })
    ).not.toBeInTheDocument()
    expect(useOrgMembers).not.toHaveBeenCalled()
  })

  it("lists only addable users in the add members dialog", async () => {
    const user = renderGroups()
    const dialog = await openAddMembersDialog(user)

    expect(useOrgMembers).toHaveBeenCalled()
    expect(
      within(dialog).getByText("Add members - Operators")
    ).toBeInTheDocument()
    expect(within(dialog).getByText("three@example.com")).toBeInTheDocument()
    expect(within(dialog).getByText("four@example.com")).toBeInTheDocument()
    expect(
      within(dialog).queryByText("one@example.com")
    ).not.toBeInTheDocument()
    expect(
      within(dialog).queryByText("invited@example.com")
    ).not.toBeInTheDocument()
    expect(within(dialog).getByText("Admin")).toBeInTheDocument()
    expect(
      within(dialog).getByRole("button", { name: "Add members" })
    ).toBeDisabled()
  })

  it("filters the add members dialog by email or name", async () => {
    const user = renderGroups()
    const dialog = await openAddMembersDialog(user)
    const search = within(dialog).getByRole("textbox", {
      name: "Search members",
    })

    await user.type(search, "Three")
    expect(within(dialog).getByText("three@example.com")).toBeInTheDocument()
    expect(
      within(dialog).queryByText("four@example.com")
    ).not.toBeInTheDocument()

    await user.clear(search)
    await user.type(search, "nobody")
    expect(
      within(dialog).getByText("No members match your search")
    ).toBeInTheDocument()
  })

  it("shows the empty state when everyone is already in the group", async () => {
    jest.mocked(useOrgMembers).mockReturnValue({
      orgMembers: orgMembers.slice(0, 1),
    } as unknown as ReturnType<typeof useOrgMembers>)
    const user = renderGroups()
    const dialog = await openAddMembersDialog(user)

    expect(within(dialog).getByText("No members to add")).toBeInTheDocument()
  })

  it("adds every selected user and closes the dialog", async () => {
    const user = renderGroups()
    const dialog = await openAddMembersDialog(user)

    await user.click(within(dialog).getByText("three@example.com"))
    expect(
      within(dialog).getByRole("button", { name: "Add 1 member" })
    ).toBeEnabled()
    await user.click(
      within(dialog).getByRole("checkbox", { name: "Select four@example.com" })
    )
    await user.click(
      within(dialog).getByRole("button", { name: "Add 2 members" })
    )

    expect(addGroupMembers).toHaveBeenCalledTimes(1)
    expect(addGroupMembers).toHaveBeenCalledWith({
      groupId: "group-1",
      userIds: ["user-3", "user-4"],
    })
    expect(screen.queryByRole("dialog")).not.toBeInTheDocument()
  })

  it("keeps failed users selected after a partial failure", async () => {
    addGroupMembers.mockResolvedValue({ failedUserIds: ["user-4"] })
    const user = renderGroups()
    const dialog = await openAddMembersDialog(user)

    await user.click(
      within(dialog).getByRole("checkbox", { name: "Select all" })
    )
    await user.click(
      within(dialog).getByRole("button", { name: "Add 2 members" })
    )

    expect(screen.getByRole("dialog")).toBeInTheDocument()
    expect(
      within(dialog).getByRole("checkbox", { name: "Select four@example.com" })
    ).toBeChecked()
    expect(
      within(dialog).getByRole("checkbox", { name: "Select three@example.com" })
    ).not.toBeChecked()
    expect(
      within(dialog).getByRole("button", { name: "Add 1 member" })
    ).toBeEnabled()
  })

  it("shows the empty roles state", async () => {
    const user = renderGroups()
    await user.click(screen.getByText("Directory team"))

    expect(screen.getByText("No roles")).toBeInTheDocument()
  })
})
