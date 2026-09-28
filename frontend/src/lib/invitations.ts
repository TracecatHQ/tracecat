import type { InvitationGrant } from "@/client"

type NamedById = { id: string; name: string }

function grantRoleName(grant: InvitationGrant, roles: NamedById[] | null) {
  const name = roles?.find((r) => r.id === grant.role_id)?.name
  if (name) {
    return name
  }
  // A loaded roles list hides only the implicit organization-member role.
  if (roles && !grant.workspace_id) {
    return "Member"
  }
  return "Role"
}

/**
 * Label one grant as "Organization: Role" or "Workspace name: Role".
 * Pass `null` roles while they are loading or failed to load.
 */
export function invitationGrantLabel(
  grant: InvitationGrant,
  roles: NamedById[] | null,
  workspaces: NamedById[]
): string {
  const scope = grant.workspace_id
    ? (workspaces.find((w) => w.id === grant.workspace_id)?.name ?? "Workspace")
    : "Organization"
  return `${scope}: ${grantRoleName(grant, roles)}`
}

/** Format every grant on an invitation as compact middot-separated text. */
export function invitationGrantsSummary(
  invitation: { grants: InvitationGrant[] },
  roles: NamedById[] | null,
  workspaces: NamedById[]
): string {
  if (invitation.grants.length === 0) {
    return "Organization: Member"
  }
  return invitation.grants
    .map((grant) => invitationGrantLabel(grant, roles, workspaces))
    .join(" · ")
}
