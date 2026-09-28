import { invitationGrantLabel } from "@/lib/invitations"

const roles = [{ id: "admin", name: "Admin" }]
const workspaces = [{ id: "ws", name: "Security" }]

describe("invitationGrantLabel", () => {
  it("labels a listed role", () => {
    expect(invitationGrantLabel({ role_id: "admin" }, roles, workspaces)).toBe(
      "Organization: Admin"
    )
  })

  it("labels a hidden org-wide role as the member baseline", () => {
    expect(
      invitationGrantLabel({ role_id: "org-member" }, roles, workspaces)
    ).toBe("Organization: Member")
  })

  it("keeps the generic label for an unknown workspace role", () => {
    expect(
      invitationGrantLabel(
        { workspace_id: "ws", role_id: "gone" },
        roles,
        workspaces
      )
    ).toBe("Security: Role")
  })
})
