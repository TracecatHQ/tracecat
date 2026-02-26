"use client"

import { AlertCircle, CheckCircle2, Clock, UserPlus, UserX } from "lucide-react"
import Image from "next/image"
import Link from "next/link"
import { useRouter, useSearchParams } from "next/navigation"
import TracecatIcon from "public/icon.png"
import { Suspense } from "react"
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
import { toast } from "@/components/ui/use-toast"
import { useAuth, useAuthActions } from "@/hooks/use-auth"
import {
  useAcceptInvitation,
  useInvitationByToken,
} from "@/hooks/use-invitations"

function AcceptInvitationContent() {
  const searchParams = useSearchParams()
  const token = searchParams?.get("token") ?? null
  const router = useRouter()
  const { user, userIsLoading } = useAuth()
  const { logout } = useAuthActions()

  const acceptInvitation = useAcceptInvitation()

  const {
    data: invitation,
    isLoading: invitationIsLoading,
    error: invitationError,
  } = useInvitationByToken(token)

  // No token provided
  if (!token) {
    return (
      <Card className="w-full max-w-md">
        <CardHeader className="items-center text-center">
          <AlertCircle className="mb-4 size-12 text-destructive" />
          <CardTitle>Invalid invitation</CardTitle>
          <CardDescription>
            No invitation token was provided. Please check the link and try
            again.
          </CardDescription>
        </CardHeader>
        <CardFooter className="justify-center">
          <Button asChild variant="outline">
            <Link href="/">Go to home</Link>
          </Button>
        </CardFooter>
      </Card>
    )
  }

  // Loading states
  if (invitationIsLoading || userIsLoading) {
    return <CenteredSpinner />
  }

  // Invitation not found or error
  if (invitationError || !invitation) {
    return (
      <Card className="w-full max-w-md">
        <CardHeader className="items-center text-center">
          <AlertCircle className="mb-4 size-12 text-destructive" />
          <CardTitle>Invitation not found</CardTitle>
          <CardDescription>
            This invitation may have expired or been revoked. Please contact
            your administrator for a new invitation.
          </CardDescription>
        </CardHeader>
        <CardFooter className="justify-center">
          <Button asChild variant="outline">
            <Link href="/">Go to home</Link>
          </Button>
        </CardFooter>
      </Card>
    )
  }

  const isWorkspace = invitation.workspace_id != null
  const organizationName = invitation.organization_name
  const workspaceName = invitation.workspace_name
  const targetLabel = isWorkspace
    ? `the ${workspaceName} workspace`
    : organizationName
  const adminLabel = isWorkspace ? "workspace" : "organization"

  const handleAccept = () => {
    acceptInvitation.mutate(token, {
      onSuccess: () => {
        toast({
          title: "Invitation accepted",
          description: isWorkspace
            ? `You've joined the ${workspaceName} workspace`
            : `You've joined ${organizationName}`,
        })
        if (isWorkspace && invitation.workspace_id) {
          router.push(`/workspaces/${invitation.workspace_id}`)
        } else {
          router.push("/workspaces")
        }
      },
      onError: (error: Error) => {
        toast({
          title: "Failed to accept invitation",
          description: error.message,
          variant: "destructive",
        })
      },
    })
  }

  const isPending = acceptInvitation.isPending

  // Invitation already accepted
  if (invitation.status === "accepted") {
    return (
      <Card className="w-full max-w-md">
        <CardHeader className="items-center text-center">
          <CheckCircle2 className="mb-4 size-12 text-green-500" />
          <CardTitle>Invitation already accepted</CardTitle>
          <CardDescription>
            This invitation has already been accepted.
          </CardDescription>
        </CardHeader>
        <CardFooter className="justify-center">
          <Button asChild>
            <Link href="/workspaces">Go to workspaces</Link>
          </Button>
        </CardFooter>
      </Card>
    )
  }

  // Invitation revoked
  if (invitation.status === "revoked") {
    return (
      <Card className="w-full max-w-md">
        <CardHeader className="items-center text-center">
          <AlertCircle className="mb-4 size-12 text-destructive" />
          <CardTitle>Invitation revoked</CardTitle>
          <CardDescription>
            This invitation has been revoked. Please contact the {adminLabel}{" "}
            administrator for a new invitation.
          </CardDescription>
        </CardHeader>
        <CardFooter className="justify-center">
          <Button asChild variant="outline">
            <Link href="/">Go to home</Link>
          </Button>
        </CardFooter>
      </Card>
    )
  }

  // Check if invitation is expired
  const expiresAt = new Date(invitation.expires_at)
  const isExpired = expiresAt < new Date()

  if (isExpired) {
    return (
      <Card className="w-full max-w-md">
        <CardHeader className="items-center text-center">
          <Clock className="mb-4 size-12 text-muted-foreground" />
          <CardTitle>Invitation expired</CardTitle>
          <CardDescription>
            This invitation expired on {expiresAt.toLocaleDateString()}. Please
            contact the {adminLabel} administrator for a new invitation.
          </CardDescription>
        </CardHeader>
        <CardFooter className="justify-center">
          <Button asChild variant="outline">
            <Link href="/">Go to home</Link>
          </Button>
        </CardFooter>
      </Card>
    )
  }

  // User not authenticated - show sign in prompt
  if (!user) {
    const returnUrl = `/invitations/accept?token=${token}`
    return (
      <Card className="w-full max-w-md">
        <CardHeader className="items-center text-center">
          <Image src={TracecatIcon} alt="Tracecat" className="mb-4 size-16" />
          <CardTitle>You&apos;ve been invited!</CardTitle>
          <CardDescription>
            {invitation.inviter_name ? (
              <>
                <strong>{invitation.inviter_name}</strong> has invited you to
                join {isWorkspace ? "the " : ""}
                <strong>{targetLabel}</strong>
                {isWorkspace && (
                  <>
                    {" "}
                    in <strong>{organizationName}</strong>
                  </>
                )}{" "}
                as a <strong>{invitation.role_name}</strong>.
              </>
            ) : (
              <>
                You&apos;ve been invited to join {isWorkspace ? "the " : ""}
                <strong>{targetLabel}</strong>
                {isWorkspace && (
                  <>
                    {" "}
                    in <strong>{organizationName}</strong>
                  </>
                )}{" "}
                as a <strong>{invitation.role_name}</strong>.
              </>
            )}
          </CardDescription>
        </CardHeader>
        <CardContent className="space-y-4">
          <div className="rounded-lg border bg-muted/50 p-4">
            <div className="space-y-2 text-sm">
              <div className="flex justify-between">
                <span className="text-muted-foreground">Organization</span>
                <span className="font-medium">{organizationName}</span>
              </div>
              {workspaceName && (
                <div className="flex justify-between">
                  <span className="text-muted-foreground">Workspace</span>
                  <span className="font-medium">{workspaceName}</span>
                </div>
              )}
              <div className="flex justify-between">
                <span className="text-muted-foreground">Role</span>
                <span className="font-medium capitalize">
                  {invitation.role_name}
                </span>
              </div>
              {invitation.inviter_email && (
                <div className="flex justify-between">
                  <span className="text-muted-foreground">Invited by</span>
                  <span className="font-medium">
                    {invitation.inviter_email}
                  </span>
                </div>
              )}
              <div className="flex justify-between">
                <span className="text-muted-foreground">Expires</span>
                <span className="font-medium">
                  {expiresAt.toLocaleDateString()}
                </span>
              </div>
            </div>
          </div>
        </CardContent>
        <CardFooter className="flex-col gap-3">
          <Button asChild className="w-full">
            <Link href={`/sign-in?returnUrl=${encodeURIComponent(returnUrl)}`}>
              Sign in to accept
            </Link>
          </Button>
          <p className="text-center text-sm text-muted-foreground">
            Don&apos;t have an account?{" "}
            <Link
              href={`/sign-up?returnUrl=${encodeURIComponent(returnUrl)}`}
              className="underline hover:text-foreground"
            >
              Sign up
            </Link>
          </p>
        </CardFooter>
      </Card>
    )
  }

  // User authenticated but email doesn't match
  if (invitation.email_matches === false) {
    return (
      <Card className="w-full max-w-md">
        <CardHeader className="items-center text-center">
          <UserX className="mb-4 size-12 text-destructive" />
          <CardTitle>Wrong account</CardTitle>
          <CardDescription>
            This invitation was sent to a different email address. Please sign
            out and sign in with the correct account.
          </CardDescription>
        </CardHeader>
        <CardContent className="space-y-4">
          <div className="rounded-lg border bg-muted/50 p-4">
            <div className="space-y-2 text-sm">
              {workspaceName && (
                <div className="flex justify-between">
                  <span className="text-muted-foreground">Workspace</span>
                  <span className="font-medium">{workspaceName}</span>
                </div>
              )}
              {!isWorkspace && (
                <div className="flex justify-between">
                  <span className="text-muted-foreground">Organization</span>
                  <span className="font-medium">{organizationName}</span>
                </div>
              )}
              <div className="flex justify-between">
                <span className="text-muted-foreground">Signed in as</span>
                <span className="font-medium">{user.email}</span>
              </div>
            </div>
          </div>
        </CardContent>
        <CardFooter className="flex-col gap-3">
          <Button
            className="w-full"
            onClick={() => {
              const returnUrl = `/invitations/accept?token=${token}`
              logout(`/sign-in?returnUrl=${encodeURIComponent(returnUrl)}`)
            }}
          >
            Sign in with a different account
          </Button>
          <Button asChild variant="ghost" className="w-full">
            <Link href="/workspaces">Go to workspaces</Link>
          </Button>
        </CardFooter>
      </Card>
    )
  }

  // User authenticated - show accept button
  return (
    <Card className="w-full max-w-md">
      <CardHeader className="items-center text-center">
        <UserPlus className="mb-4 size-12 text-primary" />
        <CardTitle>Join {targetLabel}</CardTitle>
        <CardDescription>
          {invitation.inviter_name ? (
            <>
              <strong>{invitation.inviter_name}</strong> has invited you to join
              {isWorkspace ? " this workspace" : " this organization"} as a{" "}
              <strong>{invitation.role_name}</strong>.
            </>
          ) : (
            <>
              You&apos;ve been invited to join
              {isWorkspace ? " this workspace" : " this organization"} as a{" "}
              <strong>{invitation.role_name}</strong>.
            </>
          )}
        </CardDescription>
      </CardHeader>
      <CardContent className="space-y-4">
        <div className="rounded-lg border bg-muted/50 p-4">
          <div className="space-y-2 text-sm">
            <div className="flex justify-between">
              <span className="text-muted-foreground">Organization</span>
              <span className="font-medium">{organizationName}</span>
            </div>
            {workspaceName && (
              <div className="flex justify-between">
                <span className="text-muted-foreground">Workspace</span>
                <span className="font-medium">{workspaceName}</span>
              </div>
            )}
            <div className="flex justify-between">
              <span className="text-muted-foreground">Role</span>
              <span className="font-medium capitalize">
                {invitation.role_name}
              </span>
            </div>
            {invitation.inviter_email && (
              <div className="flex justify-between">
                <span className="text-muted-foreground">Invited by</span>
                <span className="font-medium">{invitation.inviter_email}</span>
              </div>
            )}
            <div className="flex justify-between">
              <span className="text-muted-foreground">Expires</span>
              <span className="font-medium">
                {expiresAt.toLocaleDateString()}
              </span>
            </div>
          </div>
        </div>
      </CardContent>
      <CardFooter className="flex-col gap-3">
        <Button className="w-full" onClick={handleAccept} disabled={isPending}>
          {isPending ? "Accepting..." : "Accept invitation"}
        </Button>
        <Button asChild variant="ghost" className="w-full">
          <Link href="/workspaces">Decline</Link>
        </Button>
      </CardFooter>
    </Card>
  )
}

export default function AcceptInvitationPage() {
  return (
    <div className="flex min-h-screen items-center justify-center p-4">
      <Suspense fallback={<CenteredSpinner />}>
        <AcceptInvitationContent />
      </Suspense>
    </div>
  )
}
