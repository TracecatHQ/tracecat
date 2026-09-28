import type { InvitationGrant } from "@/client"

type NamedById = { id: string; name: string }

/** Label one grant as "Organization: Role" or "Workspace name: Role". */
export function invitationGrantLabel(
  grant: InvitationGrant,
  roles: NamedById[],
  workspaces: NamedById[]
): string {
  const scope = grant.workspace_id
    ? (workspaces.find((w) => w.id === grant.workspace_id)?.name ?? "Workspace")
    : "Organization"
  // The roles list hides the implicit organization-member role.
  const role =
    roles.find((r) => r.id === grant.role_id)?.name ??
    (grant.workspace_id ? "Role" : "Member")
  return `${scope}: ${role}`
}

/** Format every grant on an invitation as compact middot-separated text. */
export function invitationGrantsSummary(
  invitation: { grants: InvitationGrant[] },
  roles: NamedById[],
  workspaces: NamedById[]
): string {
  if (invitation.grants.length === 0) {
    return "Organization: Member"
  }
  return invitation.grants
    .map((grant) => invitationGrantLabel(grant, roles, workspaces))
    .join(" · ")
}
