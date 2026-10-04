/**
 * @jest-environment jsdom
 */

import { render, screen, waitFor } from "@testing-library/react"
import userEvent from "@testing-library/user-event"
import {
  ApiError,
  type RoleReadWithScopes,
  usersSearchUser,
  type WorkspaceRead,
} from "@/client"
import { useScopeCheck } from "@/components/auth/scope-guard"
import { toast } from "@/components/ui/use-toast"
import { AddWorkspaceMember } from "@/components/workspaces/add-workspace-member"
import { useWorkspaceMutations } from "@/hooks/use-workspace"
import { useRbacRoles } from "@/lib/hooks"

jest.mock("@/client", () => ({
  ...jest.requireActual("@/client"),
  usersSearchUser: jest.fn(),
}))

jest.mock("@/components/auth/scope-guard", () => ({
  useScopeCheck: jest.fn(),
}))

jest.mock("@/components/ui/use-toast", () => ({
  toast: jest.fn(),
}))

jest.mock("@/hooks/use-workspace", () => ({
  useWorkspaceMutations: jest.fn(),
}))

jest.mock("@/lib/hooks", () => ({
  useRbacRoles: jest.fn(),
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

const WORKSPACE = { id: "ws-1", name: "SecOps" } as WorkspaceRead
const EDITOR_ROLE_ID = "33333333-3333-3333-3333-333333333333"
const ROLES = [
  { id: EDITOR_ROLE_ID, name: "Editor", slug: "workspace-editor", scopes: [] },
] as unknown as RoleReadWithScopes[]

const mockUseScopeCheck = useScopeCheck as jest.MockedFunction<
  typeof useScopeCheck
>
const mockUsersSearchUser = usersSearchUser as jest.MockedFunction<
  typeof usersSearchUser
>
const mockToast = toast as jest.MockedFunction<typeof toast>
const mockUseWorkspaceMutations = useWorkspaceMutations as jest.MockedFunction<
  typeof useWorkspaceMutations
>
const mockUseRbacRoles = useRbacRoles as jest.MockedFunction<
  typeof useRbacRoles
>

function notFound(): ApiError {
  return new ApiError(
    { method: "GET", url: "/" },
    {
      url: "/",
      ok: false,
      status: 404,
      statusText: "Not Found",
      body: { detail: "Resource not found" },
    },
    "Not Found"
  )
}

function grantScopes(granted: string[]) {
  mockUseScopeCheck.mockImplementation((scope?: string) =>
    scope ? granted.includes(scope) : true
  )
}

let addMember: jest.Mock
let inviteMember: jest.Mock

beforeEach(() => {
  addMember = jest.fn().mockResolvedValue(undefined)
  inviteMember = jest.fn().mockResolvedValue(undefined)
  mockUseWorkspaceMutations.mockReturnValue({
    addMember,
    inviteMember,
  } as unknown as ReturnType<typeof useWorkspaceMutations>)
  mockUseRbacRoles.mockReturnValue({
    roles: ROLES,
    isLoading: false,
  } as unknown as ReturnType<typeof useRbacRoles>)
})

afterEach(() => {
  jest.clearAllMocks()
})

async function submitEmail(email: string) {
  const user = userEvent.setup()
  render(<AddWorkspaceMember workspace={WORKSPACE} />)
  await user.click(screen.getByRole("button", { name: /Add member/ }))
  await user.type(screen.getByPlaceholderText("user@example.com"), email)
  const buttons = screen.getAllByRole("button", { name: /Add member/ })
  await user.click(buttons[buttons.length - 1])
}

describe("AddWorkspaceMember", () => {
  it("shows a neutral toast instead of the dialog without invite scopes", async () => {
    grantScopes([])
    const user = userEvent.setup()
    render(<AddWorkspaceMember workspace={WORKSPACE} />)

    await user.click(screen.getByRole("button", { name: /Add member/ }))

    expect(mockToast).toHaveBeenCalledTimes(1)
    expect(mockToast.mock.calls[0][0]).not.toHaveProperty("variant")
    expect(
      screen.queryByPlaceholderText("user@example.com")
    ).not.toBeInTheDocument()
  })

  it("adds an existing organization member to the workspace", async () => {
    grantScopes(["workspace:member:invite"])
    mockUsersSearchUser.mockResolvedValue({
      id: "user-1",
    } as Awaited<ReturnType<typeof usersSearchUser>>)

    await submitEmail("a@b.com")

    await waitFor(() =>
      expect(addMember).toHaveBeenCalledWith({
        workspaceId: WORKSPACE.id,
        requestBody: { user_id: "user-1" },
      })
    )
    expect(inviteMember).not.toHaveBeenCalled()
  })

  it("invites unknown users into the organization for org inviters", async () => {
    grantScopes(["org:member:invite"])
    mockUsersSearchUser.mockRejectedValue(notFound())

    await submitEmail("new@b.com")

    await waitFor(() =>
      expect(inviteMember).toHaveBeenCalledWith({
        email: "new@b.com",
        grants: [{ workspace_id: WORKSPACE.id, role_id: EDITOR_ROLE_ID }],
      })
    )
    expect(addMember).not.toHaveBeenCalled()
  })

  it("invites users outside the organization when membership returns 404", async () => {
    grantScopes(["org:member:invite"])
    mockUsersSearchUser.mockResolvedValue({
      id: "user-2",
    } as Awaited<ReturnType<typeof usersSearchUser>>)
    addMember.mockRejectedValue(notFound())

    await submitEmail("other@b.com")

    await waitFor(() =>
      expect(inviteMember).toHaveBeenCalledWith({
        email: "other@b.com",
        grants: [{ workspace_id: WORKSPACE.id, role_id: EDITOR_ROLE_ID }],
      })
    )
  })

  it("asks for an org admin when the caller cannot invite to the organization", async () => {
    grantScopes(["workspace:member:invite"])
    mockUsersSearchUser.mockRejectedValue(notFound())

    await submitEmail("new@b.com")

    expect(
      await screen.findByText(/Ask an organization admin to invite them/)
    ).toBeInTheDocument()
    expect(inviteMember).not.toHaveBeenCalled()
  })
})
