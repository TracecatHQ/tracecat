"use client"

import {
  CopyIcon,
  PlusIcon,
  ReloadIcon,
  TrashIcon,
} from "@radix-ui/react-icons"
import { useMemo, useState } from "react"
import type { AdminOrgInvitationCreate, AdminOrgInvitationRead } from "@/client"
import {
  AlertDialog,
  AlertDialogAction,
  AlertDialogCancel,
  AlertDialogContent,
  AlertDialogDescription,
  AlertDialogFooter,
  AlertDialogHeader,
  AlertDialogTitle,
} from "@/components/ui/alert-dialog"
import { Badge } from "@/components/ui/badge"
import { Button } from "@/components/ui/button"
import {
  Dialog,
  DialogContent,
  DialogDescription,
  DialogFooter,
  DialogHeader,
  DialogTitle,
} from "@/components/ui/dialog"
import { Input } from "@/components/ui/input"
import {
  Select,
  SelectContent,
  SelectGroup,
  SelectItem,
  SelectTrigger,
  SelectValue,
} from "@/components/ui/select"
import {
  Table,
  TableBody,
  TableCell,
  TableHead,
  TableHeader,
  TableRow,
} from "@/components/ui/table"
import { toast } from "@/components/ui/use-toast"
import { useAdminOrgInvitations } from "@/hooks/use-admin"

type PlatformRoleSlug = NonNullable<AdminOrgInvitationCreate["role_slug"]>

const ROLE_OPTIONS: Array<{ label: string; value: PlatformRoleSlug }> = [
  { label: "Organization owner", value: "organization-owner" },
  { label: "Organization admin", value: "organization-admin" },
  { label: "Organization member", value: "organization-member" },
]

function invitationUrl(token: string) {
  const origin = typeof window === "undefined" ? "" : window.location.origin
  return `${origin}/invitations/accept?token=${encodeURIComponent(token)}`
}

function statusVariant(status: AdminOrgInvitationRead["status"]) {
  switch (status) {
    case "pending":
      return "default"
    case "accepted":
      return "secondary"
    case "revoked":
      return "outline"
    default:
      return "secondary"
  }
}

function formatStatus(status: AdminOrgInvitationRead["status"]) {
  return status.charAt(0).toUpperCase() + status.slice(1)
}

/** Dialog for platform admins to issue and revoke organization invitations. */
export function AdminOrgInvitationsDialog({
  orgId,
  open,
  onOpenChange,
}: {
  orgId: string
  open: boolean
  onOpenChange: (open: boolean) => void
}) {
  const [email, setEmail] = useState("")
  const [roleSlug, setRoleSlug] =
    useState<PlatformRoleSlug>("organization-owner")
  const [createdToken, setCreatedToken] = useState<string | null>(null)
  const [revokeTarget, setRevokeTarget] =
    useState<AdminOrgInvitationRead | null>(null)
  const {
    invitations,
    isLoading,
    createInvitation,
    createPending,
    getInvitationToken,
    revokeInvitation,
    revokePending,
  } = useAdminOrgInvitations(orgId)

  const createdLink = useMemo(
    () => (createdToken ? invitationUrl(createdToken) : null),
    [createdToken]
  )

  async function copyToClipboard(text: string, description: string) {
    try {
      await navigator.clipboard.writeText(text)
      toast({ title: "Copied", description })
    } catch {
      toast({
        title: "Failed to copy",
        description: "Clipboard access was denied or is unavailable.",
        variant: "destructive",
      })
    }
  }

  async function copyInvitationLink(invitationId: string) {
    try {
      const { token } = await getInvitationToken(invitationId)
      await copyToClipboard(invitationUrl(token), "Invitation link copied.")
    } catch {
      toast({
        title: "Failed to copy invitation link",
        description: "The invitation token could not be loaded.",
        variant: "destructive",
      })
    }
  }

  async function handleCreateInvitation() {
    try {
      const invitation = await createInvitation({
        email,
        role_slug: roleSlug,
      })
      setCreatedToken(invitation.token)
      setEmail("")
      setRoleSlug("organization-owner")
      toast({ title: "Invitation created", description: invitation.email })
    } catch {
      toast({
        title: "Failed to create invitation",
        description: "Check the email, role, and existing pending invitations.",
        variant: "destructive",
      })
    }
  }

  async function handleRevokeInvitation() {
    if (!revokeTarget) return
    try {
      await revokeInvitation(revokeTarget.id)
      toast({ title: "Invitation revoked", description: revokeTarget.email })
    } catch {
      toast({
        title: "Failed to revoke invitation",
        description:
          "Only pending platform-created invitations can be revoked.",
        variant: "destructive",
      })
    } finally {
      setRevokeTarget(null)
    }
  }

  return (
    <>
      <Dialog open={open} onOpenChange={onOpenChange}>
        <DialogContent className="max-w-4xl">
          <DialogHeader>
            <DialogTitle>Organization invitations</DialogTitle>
            <DialogDescription>
              Create and manage platform-created invitations.
            </DialogDescription>
          </DialogHeader>

          <div className="flex flex-col gap-6">
            <div className="flex flex-col gap-3 rounded-md border p-4">
              <div className="grid gap-3 md:grid-cols-[minmax(0,1fr)_220px_auto]">
                <Input
                  id="admin-org-invitation-email"
                  name="email"
                  value={email}
                  onChange={(event) => setEmail(event.target.value)}
                  placeholder="user@example.com"
                  type="email"
                  autoComplete="email"
                />
                <Select
                  value={roleSlug}
                  onValueChange={(value) =>
                    setRoleSlug(value as PlatformRoleSlug)
                  }
                >
                  <SelectTrigger>
                    <SelectValue />
                  </SelectTrigger>
                  <SelectContent>
                    <SelectGroup>
                      {ROLE_OPTIONS.map((option) => (
                        <SelectItem key={option.value} value={option.value}>
                          {option.label}
                        </SelectItem>
                      ))}
                    </SelectGroup>
                  </SelectContent>
                </Select>
                <Button
                  onClick={handleCreateInvitation}
                  disabled={!email || createPending}
                >
                  {createPending ? (
                    <ReloadIcon data-icon="inline-start" />
                  ) : (
                    <PlusIcon data-icon="inline-start" />
                  )}
                  Create
                </Button>
              </div>

              {createdLink && createdToken && (
                <div className="flex flex-col gap-2 rounded-md border bg-muted/40 p-3">
                  <Input
                    id="admin-org-invitation-link"
                    name="invitation_link"
                    readOnly
                    value={createdLink}
                  />
                  <div className="flex flex-wrap gap-2">
                    <Button
                      type="button"
                      variant="outline"
                      size="sm"
                      onClick={() =>
                        copyToClipboard(
                          invitationUrl(createdToken),
                          "Invitation link copied."
                        )
                      }
                    >
                      <CopyIcon data-icon="inline-start" />
                      Copy link
                    </Button>
                    <Button
                      type="button"
                      variant="outline"
                      size="sm"
                      onClick={() =>
                        copyToClipboard(
                          createdToken,
                          "Invitation token copied."
                        )
                      }
                    >
                      <CopyIcon data-icon="inline-start" />
                      Copy token
                    </Button>
                  </div>
                </div>
              )}
            </div>

            <div className="rounded-md border">
              <Table>
                <TableHeader>
                  <TableRow>
                    <TableHead>Email</TableHead>
                    <TableHead>Role</TableHead>
                    <TableHead>Status</TableHead>
                    <TableHead>Expires</TableHead>
                    <TableHead className="w-[220px] text-right">
                      Actions
                    </TableHead>
                  </TableRow>
                </TableHeader>
                <TableBody>
                  {isLoading ? (
                    <TableRow>
                      <TableCell
                        colSpan={5}
                        className="h-20 text-center text-muted-foreground"
                      >
                        Loading invitations...
                      </TableCell>
                    </TableRow>
                  ) : invitations.length === 0 ? (
                    <TableRow>
                      <TableCell
                        colSpan={5}
                        className="h-20 text-center text-muted-foreground"
                      >
                        No platform-created invitations
                      </TableCell>
                    </TableRow>
                  ) : (
                    invitations.map((invitation) => (
                      <TableRow key={invitation.id}>
                        <TableCell className="font-medium">
                          {invitation.email}
                        </TableCell>
                        <TableCell>{invitation.role_name}</TableCell>
                        <TableCell>
                          <Badge variant={statusVariant(invitation.status)}>
                            {formatStatus(invitation.status)}
                          </Badge>
                        </TableCell>
                        <TableCell>
                          {new Date(invitation.expires_at).toLocaleDateString()}
                        </TableCell>
                        <TableCell>
                          <div className="flex justify-end gap-2">
                            <Button
                              type="button"
                              variant="outline"
                              size="sm"
                              onClick={() => copyInvitationLink(invitation.id)}
                            >
                              <CopyIcon data-icon="inline-start" />
                              Copy link
                            </Button>
                            <Button
                              type="button"
                              variant="ghost"
                              size="sm"
                              disabled={invitation.status !== "pending"}
                              onClick={() => setRevokeTarget(invitation)}
                            >
                              <TrashIcon data-icon="inline-start" />
                              Revoke
                            </Button>
                          </div>
                        </TableCell>
                      </TableRow>
                    ))
                  )}
                </TableBody>
              </Table>
            </div>
          </div>

          <DialogFooter>
            <Button
              type="button"
              variant="outline"
              onClick={() => onOpenChange(false)}
            >
              Close
            </Button>
          </DialogFooter>
        </DialogContent>
      </Dialog>

      <AlertDialog
        open={!!revokeTarget}
        onOpenChange={() => setRevokeTarget(null)}
      >
        <AlertDialogContent>
          <AlertDialogHeader>
            <AlertDialogTitle>Revoke invitation</AlertDialogTitle>
            <AlertDialogDescription>
              Revoke the invitation for {revokeTarget?.email}? This token will
              no longer allow organization access.
            </AlertDialogDescription>
          </AlertDialogHeader>
          <AlertDialogFooter>
            <AlertDialogCancel>Cancel</AlertDialogCancel>
            <AlertDialogAction
              variant="destructive"
              disabled={revokePending}
              onClick={handleRevokeInvitation}
            >
              Revoke
            </AlertDialogAction>
          </AlertDialogFooter>
        </AlertDialogContent>
      </AlertDialog>
    </>
  )
}
