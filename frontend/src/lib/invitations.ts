import type { InvitationGrantRead } from "@/client"

/** Label an invitation by its org grant, falling back to its first grant. */
export function invitationRoleName(invitation: {
  grants: InvitationGrantRead[]
}): string {
  const grants = invitation.grants
  if (grants.length === 0) {
    return "member"
  }
  const orgGrant = grants.find((grant) => !grant.workspace_id)
  return (orgGrant ?? grants[0]).role_name
}
