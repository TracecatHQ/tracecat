/**
 * @jest-environment jsdom
 */

import { fireEvent, render, screen, waitFor } from "@testing-library/react"
import type { ReactNode } from "react"
import type {
  OrgMemberRead,
  RoleReadWithScopes,
  UserRoleAssignmentReadWithDetails,
} from "@/client"
import { ManageUserRolesDialog } from "@/components/organization/org-members-table"

const BASELINE_ROLE_ID = "role-baseline"
const WORKSPACE_ROLE_ID = "role-ws-admin"
const WORKSPACE_ID = "ws-1"
const USER_ID = "user-1"

const deleteOrgMember = jest.fn()
const deleteUserAssignment = jest.fn()
const createUserAssignment = jest.fn()
const updateUserAssignment = jest.fn()
let userAssignments: UserRoleAssignmentReadWithDetails[] = []

jest.mock("@/components/auth/scope-guard", () => ({
  useScopeCheck: () => true,
}))

jest.mock("@/lib/hooks", () => ({
  useRbacUserAssignments: () => ({
    userAssignments,
    isLoading: false,
    refetchUserAssignments: jest.fn(async () => ({ data: userAssignments })),
    createUserAssignment,
    updateUserAssignment,
    deleteUserAssignment,
  }),
  useOrgMembers: () => ({ deleteOrgMember }),
  useRbacRoles: () => ({
    roles: [
      {
        id: BASELINE_ROLE_ID,
        name: "Organization Member",
        slug: "organization-member",
      },
      {
        id: WORKSPACE_ROLE_ID,
        name: "Workspace Admin",
        slug: "workspace-admin",
      },
    ] as RoleReadWithScopes[],
    isLoading: false,
  }),
  useWorkspaceManager: () => ({
    workspaces: [{ id: WORKSPACE_ID, name: "Engineering" }],
  }),
}))

jest.mock("@/components/ui/dialog", () => ({
  DialogContent: ({ children }: { children: ReactNode }) => (
    <div>{children}</div>
  ),
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
}))

jest.mock("@/components/ui/alert-dialog", () => ({
  AlertDialog: ({ open, children }: { open: boolean; children: ReactNode }) =>
    open ? <div>{children}</div> : null,
  AlertDialogAction: ({
    children,
    onClick,
  }: {
    children: ReactNode
    onClick?: () => void
  }) => (
    <button type="button" onClick={onClick}>
      {children}
    </button>
  ),
  AlertDialogCancel: ({ children }: { children: ReactNode }) => (
    <button type="button">{children}</button>
  ),
  AlertDialogContent: ({ children }: { children: ReactNode }) => (
    <div>{children}</div>
  ),
  AlertDialogDescription: ({ children }: { children: ReactNode }) => (
    <div>{children}</div>
  ),
  AlertDialogFooter: ({ children }: { children: ReactNode }) => (
    <div>{children}</div>
  ),
  AlertDialogHeader: ({ children }: { children: ReactNode }) => (
    <div>{children}</div>
  ),
  AlertDialogTitle: ({ children }: { children: ReactNode }) => (
    <h2>{children}</h2>
  ),
}))

jest.mock("@/components/ui/select", () => ({
  Select: ({ children }: { children: ReactNode }) => <div>{children}</div>,
  SelectContent: ({ children }: { children: ReactNode }) => (
    <div>{children}</div>
  ),
  SelectItem: ({ children }: { children: ReactNode }) => <div>{children}</div>,
  SelectTrigger: ({ children }: { children: ReactNode }) => (
    <div>{children}</div>
  ),
  SelectValue: () => null,
}))

jest.mock("@/components/ui/scroll-area", () => ({
  ScrollArea: ({ children }: { children: ReactNode }) => <div>{children}</div>,
}))

const member = {
  user_id: USER_ID,
  email: "member@example.com",
  status: "active",
} as OrgMemberRead

function assignment(
  overrides: Partial<UserRoleAssignmentReadWithDetails>
): UserRoleAssignmentReadWithDetails {
  return {
    id: "assignment-1",
    organization_id: "org-1",
    user_id: USER_ID,
    role_id: WORKSPACE_ROLE_ID,
    assigned_at: "2026-01-01T00:00:00Z",
    user_email: member.email,
    role_name: "Workspace Admin",
    ...overrides,
  }
}

/** Text of each row in the staged "Current role assignments" list. */
function stagedRowTexts(): string[] {
  return Array.from(
    document.querySelectorAll<HTMLElement>("div.justify-between")
  ).map((row) => row.querySelector("div.flex-col")?.textContent ?? "")
}

/** The staged-list row holding the given role name. */
function stagedRow(roleName: string): HTMLElement {
  const row = Array.from(
    document.querySelectorAll<HTMLElement>("div.justify-between")
  ).find((el) => el.textContent?.includes(roleName))
  if (!row) throw new Error(`no staged row for ${roleName}`)
  return row
}

describe("ManageUserRolesDialog baseline role", () => {
  beforeEach(() => {
    jest.clearAllMocks()
    userAssignments = [
      assignment({
        id: "assignment-baseline",
        role_id: BASELINE_ROLE_ID,
        role_name: "Organization Member",
        workspace_id: null,
      }),
      assignment({
        id: "assignment-workspace",
        role_id: WORKSPACE_ROLE_ID,
        workspace_id: WORKSPACE_ID,
        workspace_name: "Engineering",
      }),
    ]
  })

  it("hides the baseline role from the picker and the staged list", () => {
    render(<ManageUserRolesDialog member={member} onOpenChange={jest.fn()} />)

    expect(screen.getByText("Current role assignments (1)")).toBeInTheDocument()
    expect(stagedRowTexts()).toEqual(["Workspace AdminEngineering"])
    expect(screen.queryByText("Organization Member")).not.toBeInTheDocument()
  })

  it("routes to member removal once the visible roles are emptied", async () => {
    render(<ManageUserRolesDialog member={member} onOpenChange={jest.fn()} />)

    fireEvent.click(stagedRow("Workspace Admin").querySelector("button")!)
    expect(screen.getByText("No role assignments")).toBeInTheDocument()

    fireEvent.click(screen.getByRole("button", { name: "Done" }))
    fireEvent.click(screen.getByRole("button", { name: "Apply" }))

    await waitFor(() =>
      expect(deleteOrgMember).toHaveBeenCalledWith({ userId: USER_ID })
    )
    // The baseline assignment is never staged for deletion.
    expect(deleteUserAssignment).not.toHaveBeenCalled()
  })
})
