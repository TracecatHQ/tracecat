import { act, render, screen, waitFor, within } from "@testing-library/react"
import userEvent from "@testing-library/user-event"
import {
  type GroupRoleAssignmentReadWithDetails,
  type OrgMemberRead,
  type RoleReadWithScopes,
  rbacListAssignments,
  rbacListUserAssignments,
  rbacReplaceUserAssignments,
  type UserRoleAssignmentReadWithDetails,
} from "@/client"
import { CancelablePromise } from "@/client/core/CancelablePromise"
import { useScopeCheck } from "@/components/auth/scope-guard"
import { ManageUserRolesDialog } from "@/components/organization/org-members-table"
import { Dialog } from "@/components/ui/dialog"
import { useEntitlements } from "@/hooks/use-entitlements"
import {
  useRbacRoles,
  useRbacUserAssignments,
  useWorkspaceManager,
} from "@/lib/hooks"
import { QueryClient, QueryClientProvider, useQuery } from "@/lib/query"

jest.mock("@/client", () => ({
  rbacReplaceUserAssignments: jest.fn(),
  rbacListAssignments: jest.fn(),
  rbacListUserAssignments: jest.fn(),
}))
jest.mock("@/hooks/use-entitlements", () => ({ useEntitlements: jest.fn() }))
jest.mock("@/components/auth/scope-guard", () => ({ useScopeCheck: jest.fn() }))
jest.mock("@/lib/hooks", () => ({
  useRbacRoles: jest.fn(),
  useRbacUserAssignments: jest.fn(),
  useWorkspaceManager: jest.fn(),
}))
jest.mock("@/components/data-table", () => ({}))
jest.mock("@/components/organization/invite-member-dialog", () => ({}))
jest.mock("next/navigation", () => ({ useSearchParams: jest.fn() }))

const member: OrgMemberRead = {
  user_id: "user-1",
  email: "member@example.com",
  role_name: "Organization Member",
  status: "active",
}
const roles: RoleReadWithScopes[] = [
  { id: "baseline", slug: "organization-member", name: "Organization Member" },
  { id: "admin", slug: "organization-admin", name: "Organization Admin" },
  { id: "editor", slug: "workspace-editor", name: "Workspace Editor" },
].map((role) => ({
  ...role,
  organization_id: "org-1",
  is_system: true,
  created_at: "2026-01-01",
  updated_at: "2026-01-01",
  scopes: [],
}))
function assignment(
  id: string,
  roleId: string,
  workspaceId: string | null = null
): UserRoleAssignmentReadWithDetails {
  return {
    id,
    role_id: roleId,
    role_name: roles.find((role) => role.id === roleId)!.name,
    workspace_id: workspaceId,
    workspace_name: workspaceId ? `Workspace ${workspaceId}` : null,
    user_id: "user-1",
    user_email: member.email,
    organization_id: "org-1",
    assigned_at: "2026-01-01",
  }
}
const baseline = assignment("org-assignment", "baseline")
const workspaceRole = assignment("workspace-assignment", "editor", "A")
let savedAssignments: UserRoleAssignmentReadWithDetails[]
let groupAssignments: GroupRoleAssignmentReadWithDetails[]
const createUserAssignment = jest.fn()
const updateUserAssignment = jest.fn()
const deleteUserAssignment = jest.fn()
const onOpenChange = jest.fn()
const onRemoveMember = jest.fn()
const onSavingChange = jest.fn()
const operationOrder: string[] = []

function setupGroup() {
  groupAssignments = [
    {
      id: "group-assignment",
      group_id: "group-1",
      group_name: "Operators",
      role_id: "editor",
      role_name: "Workspace Editor",
      workspace_id: "A",
      workspace_name: "Workspace A",
      organization_id: "org-1",
      assigned_at: "2026-01-01",
    },
  ]
}

async function renderDialog(onCloseAutoFocus?: (event: Event) => void) {
  const client = new QueryClient({
    defaultOptions: { queries: { retry: false } },
  })
  const view = render(
    <QueryClientProvider client={client}>
      <Dialog open>
        <ManageUserRolesDialog
          member={member}
          onCloseAutoFocus={onCloseAutoFocus}
          onOpenChange={onOpenChange}
          onRemoveMember={onRemoveMember}
          onSavingChange={onSavingChange}
        />
      </Dialog>
    </QueryClientProvider>
  )
  await waitFor(() =>
    expect(
      screen.queryByText("Loading roles and group access…")
    ).not.toBeInTheDocument()
  )
  return { ...view, client, user: userEvent.setup() }
}
async function addRole(
  user: ReturnType<typeof userEvent.setup>,
  roleName: string,
  workspaceName?: string
) {
  await user.click(screen.getByRole("combobox", { name: "Role" }))
  await user.click(screen.getByRole("option", { name: roleName }))
  if (workspaceName) {
    await user.click(screen.getByRole("combobox", { name: "Scope" }))
    await user.click(screen.getByRole("option", { name: workspaceName }))
  }
  await user.click(screen.getByRole("button", { name: "Add role" }))
}

beforeEach(() => {
  jest.clearAllMocks()
  operationOrder.length = 0
  savedAssignments = [baseline, workspaceRole]
  groupAssignments = []
  jest
    .mocked(rbacListUserAssignments)
    .mockImplementation(
      () =>
        new CancelablePromise((resolve) =>
          resolve({ items: savedAssignments, total: savedAssignments.length })
        )
    )
  jest
    .mocked(rbacListAssignments)
    .mockImplementation(
      () =>
        new CancelablePromise((resolve) =>
          resolve({ items: groupAssignments, total: groupAssignments.length })
        )
    )
  jest.mocked(useEntitlements).mockReturnValue({
    hasEntitlement: () => true,
    hasEntitlementData: true,
    isLoading: false,
  })
  jest.mocked(useScopeCheck).mockReturnValue(true)
  jest
    .mocked(useRbacRoles)
    .mockReturnValue({ roles, isLoading: false, error: null } as ReturnType<
      typeof useRbacRoles
    >)
  jest.mocked(useWorkspaceManager).mockReturnValue({
    workspaces: [
      { id: "A", name: "Workspace A" },
      { id: "B", name: "Workspace B" },
    ],
  } as ReturnType<typeof useWorkspaceManager>)
  jest
    .mocked(useRbacUserAssignments)
    .mockImplementation(function useAssignments() {
      const query = useQuery({
        queryKey: ["rbac-user-assignments", member.user_id, undefined],
        queryFn: async () => savedAssignments,
      })
      return {
        userAssignments: query.data ?? [],
        isLoading: query.isLoading,
        error: query.error,
        createUserAssignment,
        updateUserAssignment,
        deleteUserAssignment,
        createUserAssignmentIsPending: false,
        createUserAssignmentError: null,
        updateUserAssignmentIsPending: false,
        updateUserAssignmentError: null,
        deleteUserAssignmentIsPending: false,
        deleteUserAssignmentError: null,
      }
    })
  jest.mocked(rbacReplaceUserAssignments).mockImplementation(
    ({ requestBody }) =>
      new CancelablePromise((resolve) => {
        operationOrder.push("replace")
        savedAssignments = requestBody.assignments.map(
          ({ role_id, workspace_id }) =>
            assignment(
              savedAssignments.find(
                (item) => item.workspace_id === workspace_id
              )?.id ?? "new-assignment",
              role_id,
              workspace_id ?? null
            )
        )
        resolve(undefined)
      })
  )
})

// Radix Select uses browser pointer APIs that jsdom does not provide.
beforeAll(() => {
  HTMLElement.prototype.hasPointerCapture = jest.fn(() => false)
  HTMLElement.prototype.setPointerCapture = jest.fn()
  HTMLElement.prototype.releasePointerCapture = jest.fn()
})

it("shows the baseline role and stages edits until Done; Cancel writes nothing", async () => {
  const { user } = await renderDialog()
  expect(screen.getByText("Organization Member")).toBeInTheDocument()
  await user.click(screen.getByRole("combobox", { name: "Role" }))
  expect(
    screen.getByRole("option", { name: "Organization Member" })
  ).toBeInTheDocument()
  await user.click(screen.getByRole("option", { name: "Organization Admin" }))
  await user.click(screen.getByRole("button", { name: "Add role" }))
  expect(screen.getByText("Organization Admin")).toBeInTheDocument()
  await user.click(screen.getByRole("button", { name: "Cancel" }))
  expect(onOpenChange).toHaveBeenCalledWith(false)
  expect(operationOrder).toEqual([])
})

it("promotes the hidden baseline by update with update-only permission", async () => {
  jest
    .mocked(useScopeCheck)
    .mockImplementation((scope) => scope !== "org:rbac:create")
  const { user } = await renderDialog()
  await addRole(user, "Organization Admin")
  expect(operationOrder).toEqual([])
  await user.click(screen.getByRole("button", { name: "Done" }))
  await waitFor(() =>
    expect(rbacReplaceUserAssignments).toHaveBeenCalledWith({
      requestBody: expect.objectContaining({
        assignments: [
          { role_id: "editor", workspace_id: "A" },
          { role_id: "admin", workspace_id: null },
        ],
        expected_assignments: [baseline, workspaceRole],
      }),
    })
  )
  expect(createUserAssignment).not.toHaveBeenCalled()
})

it("saves the whole draft in one request after confirmation", async () => {
  const { user } = await renderDialog()
  await addRole(user, "Organization Admin")
  await addRole(user, "Workspace Editor", "Workspace B")
  await user.click(
    screen.getByRole("button", {
      name: "Remove Workspace Editor from Workspace A",
    })
  )
  expect(screen.queryByRole("alertdialog")).not.toBeInTheDocument()
  expect(screen.queryByText(/This will remove/)).not.toBeInTheDocument()
  expect(operationOrder).toEqual([])
  await user.click(screen.getByRole("button", { name: "Done" }))
  expect(screen.getByRole("alertdialog")).toHaveTextContent(
    "Your other role changes will also be saved."
  )
  expect(operationOrder).toEqual([])
  await user.click(screen.getByRole("button", { name: "Confirm changes" }))
  await waitFor(() => expect(operationOrder).toEqual(["replace"]))
})

it("refreshes cached workspace member lists after saving organization roles", async () => {
  const { client, user } = await renderDialog()
  const memberKeys = [
    ["workspace", "A", "members"],
    ["workspace", "B", "members"],
  ]
  for (const queryKey of memberKeys) {
    client.setQueryData(queryKey, ["old role"])
  }
  const workspaceKey = ["workspace", "A"]
  client.setQueryData(workspaceKey, { name: "Workspace A" })

  await addRole(user, "Organization Admin")
  await user.click(screen.getByRole("button", { name: "Done" }))
  await waitFor(() => expect(onOpenChange).toHaveBeenCalledWith(false))

  for (const queryKey of memberKeys) {
    const fetchMembers = jest.fn().mockResolvedValue(["updated role"])
    const members = await client.fetchQuery({
      queryKey,
      queryFn: fetchMembers,
      staleTime: 5 * 60 * 1000,
    })
    expect(fetchMembers).toHaveBeenCalledTimes(1)
    expect(members).toEqual(["updated role"])
  }
  expect(client.getQueryState(workspaceKey)?.isInvalidated).toBe(false)
})

it("uses the member-removal confirmation for the final direct role, before any writes", async () => {
  savedAssignments = [workspaceRole]
  const { user } = await renderDialog()
  await user.click(
    screen.getByRole("button", {
      name: "Remove Workspace Editor from Workspace A",
    })
  )
  expect(screen.queryByRole("alert")).not.toBeInTheDocument()
  await user.click(screen.getByRole("button", { name: "Done" }))
  await waitFor(() => expect(onRemoveMember).toHaveBeenCalledTimes(1))
  expect(operationOrder).toEqual([])
})

it("keeps a baseline-only member unchanged when Done has no edits", async () => {
  savedAssignments = [baseline]
  const { user } = await renderDialog()
  await user.click(screen.getByRole("button", { name: "Done" }))
  expect(onOpenChange).toHaveBeenCalledWith(false)
  expect(onRemoveMember).not.toHaveBeenCalled()
  expect(operationOrder).toEqual([])
})

it("confirms removal after Done; Cancel preserves the draft without writes", async () => {
  savedAssignments = [workspaceRole]
  setupGroup()
  const { user } = await renderDialog()
  expect(await screen.findByText("via Operators")).toBeInTheDocument()
  expect(rbacListAssignments).toHaveBeenCalledWith({ userId: "user-1" })
  await user.click(
    screen.getByRole("button", {
      name: "Remove Workspace Editor from Workspace A",
    })
  )
  expect(screen.queryByRole("alertdialog")).not.toBeInTheDocument()
  expect(screen.queryByText(/This will remove/)).not.toBeInTheDocument()
  await user.click(screen.getByRole("button", { name: "Done" }))
  const confirmation = screen.getByRole("alertdialog")
  expect(confirmation).toHaveTextContent(
    "This will remove Workspace Editor in Workspace A from member@example.com."
  )
  expect(confirmation).toHaveTextContent("Access from groups is unchanged.")
  expect(operationOrder).toEqual([])
  await user.click(within(confirmation).getByRole("button", { name: "Cancel" }))
  expect(screen.queryByRole("alertdialog")).not.toBeInTheDocument()
  expect(screen.getByText("No direct roles")).toBeInTheDocument()
  expect(onOpenChange).not.toHaveBeenCalled()
  expect(operationOrder).toEqual([])
  await user.click(screen.getByRole("button", { name: "Done" }))
  await user.click(screen.getByRole("button", { name: "Confirm changes" }))
  await waitFor(() =>
    expect(rbacReplaceUserAssignments).toHaveBeenCalledTimes(1)
  )
  expect(onRemoveMember).not.toHaveBeenCalled()
})

it("rechecks group access after confirming a pending removal", async () => {
  setupGroup()
  const { user } = await renderDialog()
  await screen.findByText("via Operators")
  await user.click(
    screen.getByRole("button", {
      name: "Remove Workspace Editor from Workspace A",
    })
  )
  await user.click(screen.getByRole("button", { name: "Done" }))
  groupAssignments = []
  jest.mocked(rbacReplaceUserAssignments).mockRejectedValueOnce({ status: 409 })
  await user.click(screen.getByRole("button", { name: "Confirm changes" }))
  expect(await screen.findByRole("alert")).toHaveTextContent(
    "Roles or group access changed"
  )
  expect(screen.queryByRole("alertdialog")).not.toBeInTheDocument()
  expect(operationOrder).toEqual([])
  expect(onRemoveMember).not.toHaveBeenCalled()
})

it("confirms organization-role removal while workspace access remains", async () => {
  savedAssignments = [assignment(baseline.id, "admin"), workspaceRole]
  const { user } = await renderDialog()
  await user.click(
    screen.getByRole("button", {
      name: "Remove Organization Admin from organization",
    })
  )
  expect(screen.queryByRole("alert")).not.toBeInTheDocument()
  await user.click(screen.getByRole("button", { name: "Done" }))
  expect(screen.getByRole("alertdialog")).toHaveTextContent(
    "Organization Admin in the organization"
  )
  expect(operationOrder).toEqual([])
  await user.click(screen.getByRole("button", { name: "Confirm changes" }))
  await waitFor(() =>
    expect(rbacReplaceUserAssignments).toHaveBeenCalledTimes(1)
  )
  expect(onRemoveMember).not.toHaveBeenCalled()
})

it("requires member-remove permission for the final role", async () => {
  savedAssignments = [workspaceRole]
  jest
    .mocked(useScopeCheck)
    .mockImplementation((scope) => scope !== "org:member:remove")
  const { user } = await renderDialog()
  await user.click(
    screen.getByRole("button", {
      name: "Remove Workspace Editor from Workspace A",
    })
  )
  expect(screen.getByRole("button", { name: "Done" })).toBeDisabled()
  expect(screen.getByRole("alert")).toHaveTextContent(
    "You do not have permission to remove organization members"
  )
})

it("rejects a stale draft when group access changes before Done", async () => {
  savedAssignments = [workspaceRole]
  const { user } = await renderDialog()
  await user.click(
    screen.getByRole("button", {
      name: "Remove Workspace Editor from Workspace A",
    })
  )
  setupGroup()
  await user.click(screen.getByRole("button", { name: "Done" }))
  expect(await screen.findByRole("alert")).toHaveTextContent(
    "Roles or group access changed while this dialog was open"
  )
  expect(onRemoveMember).not.toHaveBeenCalled()
  expect(operationOrder).toEqual([])
})

it("rejects a stale draft when direct assignments change before Done", async () => {
  const { user } = await renderDialog()
  await addRole(user, "Organization Admin")
  savedAssignments = [
    ...savedAssignments,
    assignment("other-assignment", "editor", "B"),
  ]
  jest.mocked(rbacReplaceUserAssignments).mockRejectedValueOnce({ status: 409 })
  await user.click(screen.getByRole("button", { name: "Done" }))
  expect(await screen.findByRole("alert")).toHaveTextContent(
    "Roles or group access changed while this dialog was open"
  )
  expect(operationOrder).toEqual([])
})

it("blocks final-role removal when group membership cannot be loaded", async () => {
  savedAssignments = [workspaceRole]
  setupGroup()
  jest.mocked(rbacListAssignments).mockRejectedValue(new Error("Unavailable"))
  const { user } = await renderDialog()
  await waitFor(() =>
    expect(
      screen.getByText(
        "Group access cannot be checked. Only direct roles are shown."
      )
    ).toBeInTheDocument()
  )
  await user.click(
    screen.getByRole("button", {
      name: "Remove Workspace Editor from Workspace A",
    })
  )
  expect(screen.getByRole("alert")).toHaveTextContent(
    "Use Remove from organization to revoke all access"
  )
  expect(screen.getByRole("button", { name: "Done" })).toBeDisabled()
})

it("reloads current state after a failed save without retrying automatically", async () => {
  const { user } = await renderDialog()
  await addRole(user, "Organization Admin")
  await addRole(user, "Workspace Editor", "Workspace B")
  await user.click(
    screen.getByRole("button", {
      name: "Remove Workspace Editor from Workspace A",
    })
  )
  jest
    .mocked(rbacReplaceUserAssignments)
    .mockRejectedValueOnce(new Error("Unavailable"))
  await user.click(screen.getByRole("button", { name: "Done" }))
  await user.click(screen.getByRole("button", { name: "Confirm changes" }))
  expect(await screen.findByRole("alert")).toHaveTextContent(
    "Changes could not be confirmed"
  )
  expect(deleteUserAssignment).not.toHaveBeenCalled()
  expect(screen.queryByText("Organization Admin")).not.toBeInTheDocument()
  expect(screen.getByText("Workspace A")).toBeInTheDocument()
  await user.click(screen.getByRole("button", { name: "Done" }))
  expect(rbacReplaceUserAssignments).toHaveBeenCalledTimes(1)
})

it("undoes a staged baseline promotion without deleting its persisted source", async () => {
  const { user } = await renderDialog()
  await addRole(user, "Organization Admin")
  await user.click(
    screen.getByRole("button", {
      name: "Remove Organization Admin from organization",
    })
  )
  expect(screen.getByText("Organization Member")).toBeInTheDocument()
  expect(screen.queryByRole("alert")).not.toBeInTheDocument()
  await user.click(screen.getByRole("button", { name: "Done" }))
  expect(operationOrder).toEqual([])
  expect(onRemoveMember).not.toHaveBeenCalled()
})

it("allows undoing newly staged additions without delete permission", async () => {
  jest
    .mocked(useScopeCheck)
    .mockImplementation((scope) => scope !== "org:rbac:delete")
  const { user } = await renderDialog()
  await addRole(user, "Workspace Editor", "Workspace B")
  expect(
    screen.queryByRole("button", {
      name: "Remove Workspace Editor from Workspace A",
    })
  ).not.toBeInTheDocument()
  await user.click(
    screen.getByRole("button", {
      name: "Remove Workspace Editor from Workspace B",
    })
  )
  await user.click(screen.getByRole("button", { name: "Done" }))
  expect(operationOrder).toEqual([])
})

it("keeps basic promotion available without RBAC addons and does not fetch groups", async () => {
  jest.mocked(useEntitlements).mockReturnValue({
    hasEntitlement: () => false,
    hasEntitlementData: true,
    isLoading: false,
  })
  const { user } = await renderDialog()
  await addRole(user, "Organization Admin")
  await user.click(screen.getByRole("button", { name: "Done" }))
  await waitFor(() =>
    expect(rbacReplaceUserAssignments).toHaveBeenCalledWith({
      requestBody: expect.objectContaining({
        assignments: [
          { role_id: "editor", workspace_id: "A" },
          { role_id: "admin", workspace_id: null },
        ],
        expected_assignments: [baseline, workspaceRole],
      }),
    })
  )
  expect(rbacListAssignments).not.toHaveBeenCalled()
})

it("does not infer absent group paths without RBAC addons", async () => {
  savedAssignments = [workspaceRole]
  jest.mocked(useEntitlements).mockReturnValue({
    hasEntitlement: () => false,
    hasEntitlementData: true,
    isLoading: false,
  })
  const { user } = await renderDialog()
  await user.click(
    screen.getByRole("button", {
      name: "Remove Workspace Editor from Workspace A",
    })
  )
  expect(screen.getByRole("button", { name: "Done" })).toBeDisabled()
  expect(screen.getByRole("alert")).toHaveTextContent(
    "Group access cannot be checked"
  )
  expect(onRemoveMember).not.toHaveBeenCalled()
})

it("refreshes group paths when initial group loading finishes after editing starts", async () => {
  savedAssignments = [workspaceRole]
  let finishGroupRead: (value: {
    items: GroupRoleAssignmentReadWithDetails[]
    total: number
  }) => void = () => {}
  jest.mocked(rbacListAssignments).mockImplementationOnce(
    () =>
      new Promise((resolve) => {
        finishGroupRead = resolve
      }) as never
  )
  const { user } = await renderDialog()
  await user.click(
    screen.getByRole("button", {
      name: "Remove Workspace Editor from Workspace A",
    })
  )
  expect(screen.getByRole("button", { name: "Done" })).toBeDisabled()
  await act(async () => finishGroupRead({ items: [], total: 0 }))
  await waitFor(() =>
    expect(screen.getByRole("button", { name: "Done" })).toBeEnabled()
  )
  setupGroup()
  await user.click(screen.getByRole("button", { name: "Done" }))
  expect(await screen.findByRole("alert")).toHaveTextContent(
    "Roles or group access changed"
  )
  expect(onRemoveMember).not.toHaveBeenCalled()
  expect(operationOrder).toEqual([])
})

it("does not depend on another group read when saving ordinary role changes", async () => {
  setupGroup()
  const { user } = await renderDialog()
  await screen.findByText("via Operators")
  await addRole(user, "Organization Admin")
  jest
    .mocked(rbacListAssignments)
    .mockRejectedValueOnce(new Error("Unavailable"))
  await user.click(screen.getByRole("button", { name: "Done" }))
  await waitFor(() => expect(onOpenChange).toHaveBeenCalledWith(false))
  expect(rbacListAssignments).toHaveBeenCalledTimes(1)
  expect(rbacReplaceUserAssignments).toHaveBeenCalledTimes(1)
  expect(onRemoveMember).not.toHaveBeenCalled()
})

test("returns focus to the surviving menu button when the dialog unmounts", async () => {
  const trigger = document.createElement("button")
  document.body.appendChild(trigger)
  const { unmount } = await renderDialog((event) => {
    event.preventDefault()
    trigger.focus()
  })
  unmount()
  await waitFor(() => expect(trigger).toHaveFocus())
  trigger.remove()
})
