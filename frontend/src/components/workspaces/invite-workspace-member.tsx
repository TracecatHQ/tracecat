"use client"

import { Plus } from "lucide-react"
import type { WorkspaceRead } from "@/client"
import { useScopeCheck } from "@/components/auth/scope-guard"
import { BulkInviteDialog } from "@/components/members/bulk-invite-dialog"
import { Button } from "@/components/ui/button"
import { useWorkspaceInvitations } from "@/hooks/use-invitations"
import { useAppInfo, useRbacRoles } from "@/lib/hooks"

/**
 * "Invite by email" entry point for a workspace. Invites multiple people in
 * bulk and (when email is configured) sends invitation emails; otherwise the
 * admin shares the invitation links from the invitations table.
 */
export function InviteWorkspaceMember({
  workspace,
}: {
  workspace: WorkspaceRead
}) {
  const canInviteMembers = useScopeCheck("workspace:member:invite") === true
  const { appInfo } = useAppInfo()
  const { roles } = useRbacRoles()
  const { createInvitationsBulk, createInvitationsBulkIsPending } =
    useWorkspaceInvitations(workspace.id, { listEnabled: false })

  // Workspace preset roles and custom roles (custom roles have no slug prefix).
  const workspaceRoles = roles.filter(
    (r) => !r.slug || r.slug.startsWith("workspace-")
  )

  if (!canInviteMembers) {
    return null
  }

  return (
    <BulkInviteDialog
      title="Invite members"
      description={`Invite people to the ${workspace.name} workspace by email.`}
      roles={workspaceRoles}
      emailConfigured={appInfo?.smtp_configured ?? false}
      isPending={createInvitationsBulkIsPending}
      onSubmit={(params) => createInvitationsBulk(params)}
      trigger={
        <Button
          variant="outline"
          size="sm"
          className="h-7 bg-white disabled:cursor-not-allowed"
        >
          <Plus className="mr-1 h-3.5 w-3.5" />
          Invite member
        </Button>
      }
    />
  )
}
