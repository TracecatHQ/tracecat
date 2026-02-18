"use client"

import { AlertCircle, LogOut } from "lucide-react"
import Link from "next/link"
import { CenteredSpinner } from "@/components/loading/spinner"
import { Button } from "@/components/ui/button"
import {
  Card,
  CardContent,
  CardDescription,
  CardFooter,
  CardHeader,
  CardTitle,
} from "@/components/ui/card"
import { useAuthActions } from "@/hooks/use-auth"
import { usePendingOrgInvitations } from "@/hooks/use-pending-org-invitations"

export function NoOrganizationAccess() {
  const { logout } = useAuthActions()
  const {
    pendingInvitations,
    pendingInvitationsIsLoading,
    pendingInvitationsError,
  } = usePendingOrgInvitations()

  if (pendingInvitationsIsLoading) {
    return <CenteredSpinner />
  }

  const invitations = pendingInvitations ?? []

  return (
    <main className="container flex size-full max-w-[520px] flex-col items-center justify-center p-4">
      <Card className="w-full">
        <CardHeader className="items-center text-center">
          <AlertCircle className="mb-2 size-10 text-muted-foreground" />
          <CardTitle>No organization access yet</CardTitle>
          <CardDescription>
            Your account is authenticated, but it has not joined an
            organization.
          </CardDescription>
        </CardHeader>
        <CardContent className="space-y-3 text-center">
          {pendingInvitationsError ? (
            <p className="text-sm text-muted-foreground">
              Could not load pending invitations. If you have an invitation
              link, open it directly.
            </p>
          ) : invitations.length > 0 ? (
            <>
              <p className="text-sm text-muted-foreground">
                We found pending invitations for your account.
              </p>
              <div className="space-y-2">
                {invitations.map((invitation) => (
                  <div
                    key={invitation.token}
                    className="flex items-center justify-between rounded-md border p-3"
                  >
                    <div className="space-y-1">
                      <p className="text-sm font-medium">
                        {invitation.organization_name}
                      </p>
                      <p className="text-xs text-muted-foreground">
                        Role: {invitation.role_name}
                      </p>
                      <p className="text-xs text-muted-foreground">
                        Expires:{" "}
                        {new Date(invitation.expires_at).toLocaleDateString()}
                      </p>
                    </div>
                    <Button asChild size="sm" variant="outline">
                      <Link
                        href={`/invitations/accept?token=${encodeURIComponent(invitation.token)}`}
                      >
                        Review
                      </Link>
                    </Button>
                  </div>
                ))}
              </div>
            </>
          ) : (
            <p className="text-sm text-muted-foreground">
              No pending invitations were found for this account. Ask your
              organization administrator to invite this email address.
            </p>
          )}
        </CardContent>
        <CardFooter>
          <Button
            variant="outline"
            className="w-full"
            onClick={() => logout("/sign-in")}
          >
            <LogOut className="mr-2 size-4" />
            Sign out
          </Button>
        </CardFooter>
      </Card>
    </main>
  )
}
