import type { InvitationGrantRead } from "@/client"

/** Label one grant as "Organization: Role" or "Workspace name: Role". */
export function invitationGrantLabel(grant: InvitationGrantRead): string {
  const scope = grant.workspace_id
    ? (grant.workspace_name ?? "Workspace")
    : "Organization"
  return `${scope}: ${grant.role_name}`
}

/** Format every grant on an invitation as compact middot-separated text. */
export function invitationGrantsSummary(invitation: {
  grants: InvitationGrantRead[]
}): string {
  if (invitation.grants.length === 0) {
    return "Organization: Member"
  }
  return invitation.grants.map(invitationGrantLabel).join(" · ")
}
