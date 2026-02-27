"use client"

import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query"
import { AlertCircle, CheckCircle2, Clock, UserPlus, UserX } from "lucide-react"
import Image from "next/image"
import Link from "next/link"
import { useRouter, useSearchParams } from "next/navigation"
import TracecatIcon from "public/icon.png"
import { Suspense } from "react"
import {
  organizationAcceptInvitation,
  organizationGetInvitationByToken,
} from "@/client"
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

function AcceptInvitationContent() {
  const searchParams = useSearchParams()
  const token = searchParams?.get("token") ?? null
  const router = useRouter()
  const queryClient = useQueryClient()
  const { user, userIsLoading } = useAuth()
  const { logout } = useAuthActions()

  // Fetch invitation details
  const {
    data: invitation,
    isLoading: invitationIsLoading,
    error: invitationError,
  } = useQuery({
    queryKey: ["invitation", token],
    queryFn: async () => {
      if (!token) {
        throw new Error("No invitation token provided")
      }
      return await organizationGetInvitationByToken({ token })
    },
    enabled: !!token,
    retry: false,
  })

  // Accept invitation mutation
  const acceptMutation = useMutation({
    mutationFn: async () => {
      if (!token) {
        throw new Error("No invitation token")
      }
      return await organizationAcceptInvitation({
        requestBody: { token },
      })
    },
    onSuccess: () => {
      queryClient.invalidateQueries({ queryKey: ["auth"] })
      toast({
        title: "Invitation accepted",
        description: `You've joined ${invitation?.organization_name}`,
      })
      router.push("/workspaces")
    },
    onError: (error: Error) => {
      toast({
        title: "Failed to accept invitation",
        description: error.message,
        variant: "destructive",
      })
    },
  })

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
            This invitation may have expired or been revoked. Please contact the
            organization administrator for a new invitation.
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
            This invitation has been revoked. Please contact the organization
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
            contact the organization administrator for a new invitation.
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
    const signInPath = `/sign-in?returnUrl=${encodeURIComponent(returnUrl)}&org=${encodeURIComponent(invitation.organization_slug)}`
    const signUpPath = `/sign-up?returnUrl=${encodeURIComponent(returnUrl)}&org=${encodeURIComponent(invitation.organization_slug)}`
    return (
      <Card className="w-full max-w-md">
        <CardHeader className="items-center text-center">
          <Image src={TracecatIcon} alt="Tracecat" className="mb-4 size-16" />
          <CardTitle>You&apos;ve been invited!</CardTitle>
          <CardDescription>
            {invitation.inviter_name ? (
              <>
                <strong>{invitation.inviter_name}</strong> has invited you to
                join <strong>{invitation.organization_name}</strong> as a{" "}
                <strong>{invitation.role_name}</strong>.
              </>
            ) : (
              <>
                You&apos;ve been invited to join{" "}
                <strong>{invitation.organization_name}</strong> as a{" "}
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
                <span className="font-medium">
                  {invitation.organization_name}
                </span>
              </div>
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
            <Link href={signInPath}>Sign in to accept</Link>
          </Button>
          <p className="text-center text-sm text-muted-foreground">
            Don&apos;t have an account?{" "}
            <Link href={signUpPath} className="underline hover:text-foreground">
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
              <div className="flex justify-between">
                <span className="text-muted-foreground">Organization</span>
                <span className="font-medium">
                  {invitation.organization_name}
                </span>
              </div>
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
              logout(
                `/sign-in?returnUrl=${encodeURIComponent(returnUrl)}&org=${encodeURIComponent(invitation.organization_slug)}`
              )
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
        <CardTitle>Join {invitation.organization_name}</CardTitle>
        <CardDescription>
          {invitation.inviter_name ? (
            <>
              <strong>{invitation.inviter_name}</strong> has invited you to join
              this organization as a <strong>{invitation.role_name}</strong>.
            </>
          ) : (
            <>
              You&apos;ve been invited to join this organization as a{" "}
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
              <span className="font-medium">
                {invitation.organization_name}
              </span>
            </div>
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
        <Button
          className="w-full"
          onClick={() => acceptMutation.mutate()}
          disabled={acceptMutation.isPending}
        >
          {acceptMutation.isPending ? "Accepting..." : "Accept invitation"}
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
