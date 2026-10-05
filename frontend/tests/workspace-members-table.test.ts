import type { WorkspaceMember } from "@/client"
import { canChangeRole } from "@/components/workspaces/workspace-members-table"

const EDITOR_ROLE_ID = "33333333-3333-3333-3333-333333333333"
const MEMBER: WorkspaceMember = {
  user_id: "user-1",
  first_name: null,
  last_name: null,
  email: "a@b.com",
  role_id: EDITOR_ROLE_ID,
  role_name: "Editor",
  via_group: false,
  org_wide: false,
}
const CALLER = {
  canUpdateMembers: true,
  currentUserId: "caller",
  assignableRoleIds: new Set([EDITOR_ROLE_ID]),
}

describe("canChangeRole", () => {
  it("allows a direct workspace role within the caller's ceiling", () => {
    expect(canChangeRole(MEMBER, CALLER)).toBe(true)
  })

  it.each([
    ["missing update scope", MEMBER, { ...CALLER, canUpdateMembers: false }],
    ["group grant", { ...MEMBER, via_group: true }, CALLER],
    ["org-wide grant", { ...MEMBER, org_wide: true }, CALLER],
    ["the caller", MEMBER, { ...CALLER, currentUserId: MEMBER.user_id }],
    [
      "a role above the caller's ceiling",
      MEMBER,
      { ...CALLER, assignableRoleIds: new Set<string>() },
    ],
  ])("blocks %s", (_label, member, caller) => {
    expect(canChangeRole(member, caller)).toBe(false)
  })
})
