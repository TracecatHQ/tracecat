"use client"

import { AlertCircle, Building2, Loader2, Mail, UserPlus } from "lucide-react"
import { useRouter, useSearchParams } from "next/navigation"
import { Suspense, useEffect, useState } from "react"
import type { OrgInvitationRead } from "@/client"
import {
  organizationAcceptInvitation,
  organizationGetInvitationByToken,
} from "@/client"
import { Alert, AlertDescription, AlertTitle } from "@/components/ui/alert"
import { Button } from "@/components/ui/button"
import {
  Card,
  CardContent,
  CardDescription,
  CardFooter,
  CardHeader,
  CardTitle,
} from "@/components/ui/card"
import { useAuth } from "@/hooks/use-auth"

type InviteState =
  | { status: "loading" }
  | { status: "invalid"; message: string }
  | { status: "expired"; invitation: OrgInvitationRead }
  | { status: "valid"; invitation: OrgInvitationRead }
  | { status: "accepting" }
  | { status: "accepted"; invitation: OrgInvitationRead }
  | { status: "error"; message: string; invitation?: OrgInvitationRead }

function AcceptInviteContent() {
  const router = useRouter()
  const searchParams = useSearchParams()
  const token = searchParams?.get("token")
  const { user, userIsLoading: authLoading } = useAuth()

  const [state, setState] = useState<InviteState>({ status: "loading" })

  // Fetch invitation details when token is available
  useEffect(() => {
    async function fetchInvitation() {
      if (!token) {
        setState({ status: "invalid", message: "No invitation token provided" })
        return
      }

      try {
        const invitation = await organizationGetInvitationByToken({ token })

        // Check if invitation is expired
        const expiresAt = new Date(invitation.expires_at)
        if (expiresAt < new Date()) {
          setState({ status: "expired", invitation })
          return
        }

        // Check if already accepted
        if (invitation.status === "accepted") {
          setState({
            status: "invalid",
            message: "This invitation has already been accepted",
          })
          return
        }

        // Check if revoked
        if (invitation.status === "revoked") {
          setState({
            status: "invalid",
            message: "This invitation has been revoked",
          })
          return
        }

        setState({ status: "valid", invitation })
      } catch {
        setState({
          status: "invalid",
          message: "Invalid or expired invitation link",
        })
      }
    }

    fetchInvitation()
  }, [token])

  // Handle accepting the invitation
  async function handleAcceptInvitation() {
    if (!token || state.status !== "valid") return

    const invitation = state.invitation
    setState({ status: "accepting" })

    try {
      await organizationAcceptInvitation({
        requestBody: { token },
      })

      setState({ status: "accepted", invitation })

      // Redirect to workspaces after a short delay
      setTimeout(() => {
        router.push("/workspaces")
      }, 2000)
    } catch (error) {
      const message =
        error instanceof Error
          ? error.message
          : typeof error === "object" &&
              error !== null &&
              "body" in error &&
              typeof (error as { body?: { detail?: string } }).body?.detail ===
                "string"
            ? (error as { body: { detail: string } }).body.detail
            : "Failed to accept invitation"
      setState({
        status: "error",
        message,
        invitation,
      })
    }
  }

  // Redirect to sign-in/sign-up with token preserved
  function handleSignIn() {
    const redirect = encodeURIComponent(`/invite/accept?token=${token}`)
    router.push(`/sign-in?redirect=${redirect}`)
  }

  function handleSignUp() {
    const redirect = encodeURIComponent(`/invite/accept?token=${token}`)
    router.push(`/sign-up?redirect=${redirect}`)
  }

  // Loading state
  if (state.status === "loading" || authLoading) {
    return (
      <div className="flex min-h-screen items-center justify-center">
        <Card className="w-full max-w-md">
          <CardContent className="flex flex-col items-center py-10">
            <Loader2 className="h-8 w-8 animate-spin text-muted-foreground" />
            <p className="mt-4 text-sm text-muted-foreground">
              Loading invitation...
            </p>
          </CardContent>
        </Card>
      </div>
    )
  }

  // Invalid invitation
  if (state.status === "invalid") {
    return (
      <div className="flex min-h-screen items-center justify-center p-4">
        <Card className="w-full max-w-md">
          <CardHeader>
            <CardTitle className="flex items-center gap-2">
              <AlertCircle className="h-5 w-5 text-destructive" />
              Invalid invitation
            </CardTitle>
          </CardHeader>
          <CardContent>
            <Alert variant="destructive">
              <AlertCircle className="h-4 w-4" />
              <AlertTitle>Error</AlertTitle>
              <AlertDescription>{state.message}</AlertDescription>
            </Alert>
          </CardContent>
          <CardFooter>
            <Button
              variant="outline"
              onClick={() => router.push("/sign-in")}
              className="w-full"
            >
              Go to sign in
            </Button>
          </CardFooter>
        </Card>
      </div>
    )
  }

  // Expired invitation
  if (state.status === "expired") {
    return (
      <div className="flex min-h-screen items-center justify-center p-4">
        <Card className="w-full max-w-md">
          <CardHeader>
            <CardTitle className="flex items-center gap-2">
              <AlertCircle className="h-5 w-5 text-destructive" />
              Invitation expired
            </CardTitle>
          </CardHeader>
          <CardContent>
            <Alert variant="destructive">
              <AlertCircle className="h-4 w-4" />
              <AlertTitle>This invitation has expired</AlertTitle>
              <AlertDescription>
                Please contact the organization administrator to request a new
                invitation.
              </AlertDescription>
            </Alert>
          </CardContent>
          <CardFooter>
            <Button
              variant="outline"
              onClick={() => router.push("/sign-in")}
              className="w-full"
            >
              Go to sign in
            </Button>
          </CardFooter>
        </Card>
      </div>
    )
  }

  // Error state
  if (state.status === "error") {
    return (
      <div className="flex min-h-screen items-center justify-center p-4">
        <Card className="w-full max-w-md">
          <CardHeader>
            <CardTitle className="flex items-center gap-2">
              <AlertCircle className="h-5 w-5 text-destructive" />
              Error
            </CardTitle>
          </CardHeader>
          <CardContent>
            <Alert variant="destructive">
              <AlertCircle className="h-4 w-4" />
              <AlertTitle>Failed to accept invitation</AlertTitle>
              <AlertDescription>{state.message}</AlertDescription>
            </Alert>
          </CardContent>
          <CardFooter>
            <Button
              variant="outline"
              onClick={() => {
                if (state.invitation) {
                  setState({ status: "valid", invitation: state.invitation })
                }
              }}
              className="w-full"
            >
              Try again
            </Button>
          </CardFooter>
        </Card>
      </div>
    )
  }

  // Accepted state
  if (state.status === "accepted") {
    return (
      <div className="flex min-h-screen items-center justify-center p-4">
        <Card className="w-full max-w-md">
          <CardHeader>
            <CardTitle className="flex items-center gap-2 text-green-600">
              <UserPlus className="h-5 w-5" />
              Welcome!
            </CardTitle>
            <CardDescription>
              You&apos;ve joined the organization
            </CardDescription>
          </CardHeader>
          <CardContent className="text-center">
            <p className="text-muted-foreground">
              Redirecting you to your workspaces...
            </p>
            <Loader2 className="mx-auto mt-4 h-6 w-6 animate-spin" />
          </CardContent>
        </Card>
      </div>
    )
  }

  // Valid invitation - show details and accept button
  const invitation = state.status === "valid" ? state.invitation : null
  if (!invitation) return null

  return (
    <div className="flex min-h-screen items-center justify-center p-4">
      <Card className="w-full max-w-md">
        <CardHeader>
          <CardTitle className="flex items-center gap-2">
            <Mail className="h-5 w-5" />
            You&apos;ve been invited
          </CardTitle>
          <CardDescription>Join an organization on Tracecat</CardDescription>
        </CardHeader>
        <CardContent className="space-y-4">
          <div className="rounded-lg border bg-muted/50 p-4">
            <div className="flex items-center gap-3">
              <div className="flex h-10 w-10 items-center justify-center rounded-full bg-primary/10">
                <Building2 className="h-5 w-5 text-primary" />
              </div>
              <div>
                <p className="font-medium">Organization invitation</p>
                <p className="text-sm text-muted-foreground">
                  Role: <span className="capitalize">{invitation.role}</span>
                </p>
              </div>
            </div>
          </div>

          <div className="text-sm text-muted-foreground">
            <p>Invited: {invitation.email}</p>
            <p>
              Expires:{" "}
              {new Date(invitation.expires_at).toLocaleDateString(undefined, {
                year: "numeric",
                month: "long",
                day: "numeric",
              })}
            </p>
          </div>
        </CardContent>
        <CardFooter className="flex flex-col gap-3">
          {user ? (
            // User is logged in - show accept button
            <Button
              onClick={handleAcceptInvitation}
              disabled={state.status === "accepting"}
              className="w-full"
            >
              {state.status === "accepting" ? (
                <>
                  <Loader2 className="mr-2 h-4 w-4 animate-spin" />
                  Accepting...
                </>
              ) : (
                <>
                  <UserPlus className="mr-2 h-4 w-4" />
                  Accept invitation
                </>
              )}
            </Button>
          ) : (
            // User is not logged in - show sign in/sign up options
            <>
              <p className="text-center text-sm text-muted-foreground">
                Sign in or create an account to accept this invitation
              </p>
              <div className="flex w-full gap-2">
                <Button
                  variant="outline"
                  onClick={handleSignIn}
                  className="flex-1"
                >
                  Sign in
                </Button>
                <Button onClick={handleSignUp} className="flex-1">
                  Create account
                </Button>
              </div>
            </>
          )}
        </CardFooter>
      </Card>
    </div>
  )
}

export default function AcceptInvitePage() {
  return (
    <Suspense
      fallback={
        <div className="flex min-h-screen items-center justify-center">
          <Card className="w-full max-w-md">
            <CardContent className="flex flex-col items-center py-10">
              <Loader2 className="h-8 w-8 animate-spin text-muted-foreground" />
              <p className="mt-4 text-sm text-muted-foreground">
                Loading invitation...
              </p>
            </CardContent>
          </Card>
        </div>
      }
    >
      <AcceptInviteContent />
    </Suspense>
  )
}
