"use client"

import { AlertCircle, CheckCircle2, Clock, UserPlus, UserX } from "lucide-react"
import Image from "next/image"
import Link from "next/link"
import { useRouter, useSearchParams } from "next/navigation"
import TracecatIcon from "public/icon.png"
import { Suspense, useEffect, useState } from "react"
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
import { Checkbox } from "@/components/ui/checkbox"
import { toast } from "@/components/ui/use-toast"
import { useAuth, useAuthActions } from "@/hooks/use-auth"
import {
  buildInvitationAcceptUrl,
  useAcceptInvitation,
  useDeclineInvitation,
  useInvitationByToken,
} from "@/hooks/use-invitations"

function InvitationDetails({
  organizationName,
  workspaceName,
  roleName,
  inviterEmail,
  expiresAt,
  workspaceOptions,
  selectedWorkspaceIds,
  onWorkspaceToggle,
  showSelection,
}: {
  organizationName: string
  workspaceName?: string | null
  roleName: string
  inviterEmail?: string | null
  expiresAt: Date
  workspaceOptions: Array<{
    invitation_id: string
    workspace_id: string
    workspace_name?: string | null
    role_name: string
  }>
  selectedWorkspaceIds: string[]
  onWorkspaceToggle: (workspaceId: string, checked: boolean) => void
  showSelection: boolean
}) {
  return (
    <div className="space-y-4">
      <div className="rounded-lg border bg-muted/50 p-4">
        <div className="space-y-2 text-sm">
          <div className="flex justify-between gap-4">
            <span className="text-muted-foreground">Organization</span>
            <span className="font-medium">{organizationName}</span>
          </div>
          {workspaceName && (
            <div className="flex justify-between gap-4">
              <span className="text-muted-foreground">Workspace</span>
              <span className="font-medium">{workspaceName}</span>
            </div>
          )}
          <div className="flex justify-between gap-4">
            <span className="text-muted-foreground">Role</span>
            <span className="font-medium">{roleName}</span>
          </div>
          {inviterEmail && (
            <div className="flex justify-between gap-4">
              <span className="text-muted-foreground">Invited by</span>
              <span className="font-medium">{inviterEmail}</span>
            </div>
          )}
          <div className="flex justify-between gap-4">
            <span className="text-muted-foreground">Expires</span>
            <span className="font-medium">
              {expiresAt.toLocaleDateString()}
            </span>
          </div>
        </div>
      </div>
      {showSelection && workspaceOptions.length > 0 && (
        <div className="rounded-lg border p-4">
          <div className="mb-3">
            <h3 className="text-sm font-medium">Choose workspaces</h3>
            <p className="text-sm text-muted-foreground">
              Select at least one workspace to join with this organization
              invitation.
            </p>
          </div>
          <div className="space-y-3">
            {workspaceOptions.map((workspace) => (
              <label
                key={workspace.invitation_id}
                className="grid grid-cols-[auto_1fr] items-center gap-2.5 rounded-md border px-3 py-3"
              >
                <Checkbox
                  className="mt-0.5"
                  checked={selectedWorkspaceIds.includes(
                    workspace.workspace_id
                  )}
                  onCheckedChange={(checked) =>
                    onWorkspaceToggle(workspace.workspace_id, checked === true)
                  }
                />
                <div className="min-w-0 space-y-0.5">
                  <div className="text-sm font-medium leading-none">
                    {workspace.workspace_name ?? "Unnamed workspace"}
                  </div>
                  <div className="text-sm leading-tight text-muted-foreground">
                    {workspace.role_name}
                  </div>
                </div>
              </label>
            ))}
          </div>
        </div>
      )}
    </div>
  )
}

function AcceptInvitationContent() {
  const searchParams = useSearchParams()
  const queryToken = searchParams?.get("token") ?? null
  const router = useRouter()
  const { user, userIsLoading } = useAuth()
  const { logout } = useAuthActions()
  const acceptInvitation = useAcceptInvitation()
  const declineInvitation = useDeclineInvitation()

  const {
    data: invitation,
    isLoading: invitationIsLoading,
    error: invitationError,
  } = useInvitationByToken(queryToken)

  const [selectedWorkspaceIds, setSelectedWorkspaceIds] = useState<string[]>([])

  useEffect(() => {
    if (!invitation) {
      return
    }
    if (invitation.workspace_id || invitation.workspace_options.length === 0) {
      setSelectedWorkspaceIds([])
      return
    }
    setSelectedWorkspaceIds(
      invitation.workspace_options.map((workspace) => workspace.workspace_id)
    )
  }, [invitation])

  if (!queryToken) {
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

  if (invitationIsLoading || userIsLoading) {
    return <CenteredSpinner />
  }

  if (invitationError || !invitation) {
    return (
      <Card className="w-full max-w-md">
        <CardHeader className="items-center text-center">
          <AlertCircle className="mb-4 size-12 text-destructive" />
          <CardTitle>Invitation not found</CardTitle>
          <CardDescription>
            This invitation may have expired, been revoked, or already been
            handled.
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

  const expiresAt = new Date(invitation.expires_at)
  const isExpired = expiresAt < new Date()
  const invitationToken = invitation.accept_token
  const organizationName = invitation.organization_name
  const workspaceId = invitation.workspace_id
  const workspaceName = invitation.workspace_name
  const isWorkspaceInvite = invitation.workspace_id != null
  const hasWorkspaceSelection =
    invitation.workspace_id == null && invitation.workspace_options.length > 0
  const targetLabel = isWorkspaceInvite
    ? `the ${workspaceName ?? "workspace"} workspace`
    : organizationName
  const adminLabel = isWorkspaceInvite ? "workspace" : "organization"

  function handleWorkspaceToggle(workspaceId: string, checked: boolean) {
    setSelectedWorkspaceIds((current) => {
      if (checked) {
        return current.includes(workspaceId)
          ? current
          : [...current, workspaceId]
      }
      return current.filter((id) => id !== workspaceId)
    })
  }

  function handleAccept() {
    if (hasWorkspaceSelection && selectedWorkspaceIds.length === 0) {
      toast({
        title: "Select a workspace",
        description: "Choose at least one workspace before accepting.",
        variant: "destructive",
      })
      return
    }

    acceptInvitation.mutate(
      {
        token: invitationToken,
        ...(hasWorkspaceSelection ? { selectedWorkspaceIds } : {}),
      },
      {
        onSuccess: () => {
          toast({
            title: "Invitation accepted",
            description: hasWorkspaceSelection
              ? `You've joined ${organizationName}.`
              : isWorkspaceInvite
                ? `You've joined the ${workspaceName} workspace.`
                : `You've joined ${organizationName}.`,
          })
          if (isWorkspaceInvite && workspaceId) {
            router.push(`/workspaces/${workspaceId}`)
            return
          }
          router.push("/workspaces")
        },
        onError: (error: Error) => {
          toast({
            title: "Failed to accept invitation",
            description: error.message,
            variant: "destructive",
          })
        },
      }
    )
  }

  function handleDecline() {
    declineInvitation.mutate(
      { token: invitationToken },
      {
        onSuccess: () => {
          toast({
            title: "Invitation declined",
          })
          router.push("/")
        },
        onError: (error: Error) => {
          toast({
            title: "Failed to decline invitation",
            description: error.message,
            variant: "destructive",
          })
        },
      }
    )
  }

  if (invitation.status === "accepted") {
    const acceptedDestination =
      isWorkspaceInvite && workspaceId
        ? `/workspaces/${workspaceId}`
        : "/workspaces"
    const acceptedLabel = isWorkspaceInvite
      ? `Go to ${workspaceName ?? "workspace"}`
      : "Go to workspaces"

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
            <Link href={acceptedDestination}>{acceptedLabel}</Link>
          </Button>
        </CardFooter>
      </Card>
    )
  }

  if (invitation.status === "revoked" || invitation.status === "declined") {
    return (
      <Card className="w-full max-w-md">
        <CardHeader className="items-center text-center">
          <AlertCircle className="mb-4 size-12 text-destructive" />
          <CardTitle>
            {invitation.status === "declined"
              ? "Invitation declined"
              : "Invitation revoked"}
          </CardTitle>
          <CardDescription>
            {invitation.status === "declined"
              ? "This invitation has already been declined."
              : `This invitation has been revoked. Please contact the ${adminLabel} administrator for a new invitation.`}
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

  if (!user) {
    const returnUrl = buildInvitationAcceptUrl(queryToken)
    const signInPath = `/sign-in?returnUrl=${encodeURIComponent(returnUrl)}&org=${encodeURIComponent(invitation.organization_slug)}`

    return (
      <Card className="w-full max-w-xl">
        <CardHeader className="items-center text-center">
          <Image src={TracecatIcon} alt="Tracecat" className="mb-4 size-16" />
          <CardTitle>You&apos;ve been invited</CardTitle>
          <CardDescription>
            {invitation.inviter_name ? (
              <>
                <strong>{invitation.inviter_name}</strong> has invited you to
                join <strong>{targetLabel}</strong>.
              </>
            ) : (
              <>
                You&apos;ve been invited to join <strong>{targetLabel}</strong>.
              </>
            )}
          </CardDescription>
        </CardHeader>
        <CardContent>
          <InvitationDetails
            organizationName={invitation.organization_name}
            workspaceName={workspaceName}
            roleName={invitation.role_name}
            inviterEmail={invitation.inviter_email}
            expiresAt={expiresAt}
            workspaceOptions={invitation.workspace_options}
            selectedWorkspaceIds={selectedWorkspaceIds}
            onWorkspaceToggle={handleWorkspaceToggle}
            showSelection={false}
          />
        </CardContent>
        <CardFooter className="flex-col gap-3">
          <Button asChild className="w-full">
            <Link href={signInPath}>Sign in to continue</Link>
          </Button>
        </CardFooter>
      </Card>
    )
  }

  if (invitation.email_matches === false) {
    const returnUrl = buildInvitationAcceptUrl(queryToken)
    const signInPath = `/sign-in?returnUrl=${encodeURIComponent(returnUrl)}&org=${encodeURIComponent(invitation.organization_slug)}`

    return (
      <Card className="w-full max-w-xl">
        <CardHeader className="items-center text-center">
          <UserX className="mb-4 size-12 text-destructive" />
          <CardTitle>Wrong account</CardTitle>
          <CardDescription>
            This invitation was sent to a different email address. Sign in with
            the invited account to continue.
          </CardDescription>
        </CardHeader>
        <CardContent>
          <div className="rounded-lg border bg-muted/50 p-4 text-sm">
            <div className="flex justify-between gap-4">
              <span className="text-muted-foreground">Signed in as</span>
              <span className="font-medium">{user.email}</span>
            </div>
          </div>
        </CardContent>
        <CardFooter className="flex-col gap-3">
          <Button className="w-full" onClick={() => logout(signInPath)}>
            Sign in with a different account
          </Button>
          <Button asChild variant="ghost" className="w-full">
            <Link href="/workspaces">Go to workspaces</Link>
          </Button>
        </CardFooter>
      </Card>
    )
  }

  return (
    <Card className="w-full max-w-xl">
      <CardHeader className="items-center text-center">
        <UserPlus className="mb-4 size-12 text-primary" />
        <CardTitle>
          {hasWorkspaceSelection
            ? `Join ${invitation.organization_name}`
            : `Join ${targetLabel}`}
        </CardTitle>
        <CardDescription>
          {hasWorkspaceSelection
            ? "Choose which workspaces to join as part of this organization invitation."
            : `Accept this ${adminLabel} invitation to continue.`}
        </CardDescription>
      </CardHeader>
      <CardContent>
        <InvitationDetails
          organizationName={invitation.organization_name}
          workspaceName={workspaceName}
          roleName={invitation.role_name}
          inviterEmail={invitation.inviter_email}
          expiresAt={expiresAt}
          workspaceOptions={invitation.workspace_options}
          selectedWorkspaceIds={selectedWorkspaceIds}
          onWorkspaceToggle={handleWorkspaceToggle}
          showSelection={hasWorkspaceSelection}
        />
      </CardContent>
      <CardFooter className="flex-col gap-3">
        <Button
          className="w-full"
          onClick={handleAccept}
          disabled={
            acceptInvitation.isPending ||
            (hasWorkspaceSelection && selectedWorkspaceIds.length === 0)
          }
        >
          {acceptInvitation.isPending ? "Accepting..." : "Accept invitation"}
        </Button>
        <Button
          variant="ghost"
          className="w-full"
          onClick={handleDecline}
          disabled={declineInvitation.isPending}
        >
          {declineInvitation.isPending ? "Declining..." : "Decline"}
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
