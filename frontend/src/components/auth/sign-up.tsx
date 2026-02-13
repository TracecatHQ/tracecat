"use client"

import { zodResolver } from "@hookform/resolvers/zod"
import Image from "next/image"
import Link from "next/link"
import { useRouter } from "next/navigation"
import TracecatIcon from "public/icon.png"
import type React from "react"
import { useEffect, useState } from "react"
import { useForm } from "react-hook-form"
import { z } from "zod"
import {
  ApiError,
  organizationAcceptInvitation,
  organizationGetInvitationByToken,
  workspacesAcceptWorkspaceInvitation,
  workspacesGetInvitationByToken,
} from "@/client"
import { Icons } from "@/components/icons"
import { Button } from "@/components/ui/button"
import {
  CardContent,
  CardDescription,
  CardHeader,
  CardTitle,
} from "@/components/ui/card"
import {
  Form,
  FormControl,
  FormField,
  FormItem,
  FormLabel,
  FormMessage,
} from "@/components/ui/form"
import { Input } from "@/components/ui/input"
import { useAuth, useAuthActions } from "@/hooks/use-auth"
import type { RequestValidationError, TracecatApiError } from "@/lib/errors"
import { cn } from "@/lib/utils"

// Move type definition outside the function for reuse
type EmailLoginValidationError = {
  code: string
  reason: string
}

// Add type guard function
function isEmailLoginValidationError(
  detail: EmailLoginValidationError | RequestValidationError[]
): detail is EmailLoginValidationError {
  // not an array
  if (!Array.isArray(detail)) {
    return false
  }
  return (
    "code" in detail &&
    "reason" in detail &&
    typeof detail.code === "string" &&
    typeof detail.reason === "string"
  )
}

type InvitationType = "organization" | "workspace"
type ExtractedInvitation = {
  type: InvitationType
  token: string
}

interface SignUpProps extends React.HTMLProps<HTMLDivElement> {
  returnUrl?: string | null
}

export function SignUp({ className, returnUrl }: SignUpProps) {
  const { user } = useAuth()
  const router = useRouter()
  const { logout } = useAuthActions()
  const [invitationEmailMismatch, setInvitationEmailMismatch] = useState(false)
  const [invitationRedirectUrl, setInvitationRedirectUrl] = useState<
    string | null
  >(null)
  const [isProcessingInvitation, setIsProcessingInvitation] = useState(false)

  useEffect(() => {
    // Don't redirect if there's an invitation email mismatch or invitation is still being processed
    if (user && !invitationEmailMismatch && !isProcessingInvitation) {
      // If invitation was accepted, redirect to the workspace directly
      // Otherwise, redirect to returnUrl if provided, or to workspaces
      const redirectTo = invitationRedirectUrl || returnUrl || "/workspaces"
      router.push(redirectTo)
    }
  }, [
    user,
    router,
    returnUrl,
    invitationEmailMismatch,
    invitationRedirectUrl,
    isProcessingInvitation,
  ])

  // Show error state if email doesn't match invitation
  if (invitationEmailMismatch && user) {
    return (
      <div
        className={cn(
          "container flex size-full items-center justify-center",
          className
        )}
      >
        <div className="flex w-full flex-1 flex-col justify-center gap-2 px-8 sm:max-w-md">
          <CardHeader className="items-center space-y-2 text-center">
            <Image src={TracecatIcon} alt="Tracecat" className="mb-8 size-16" />
            <CardTitle className="text-2xl">Wrong account</CardTitle>
            <CardDescription>
              This invitation was sent to a different email address. Please sign
              in with the email address the invitation was sent to.
            </CardDescription>
          </CardHeader>
          <CardContent className="space-y-4">
            <div className="rounded-lg border bg-muted/50 p-4">
              <div className="space-y-2 text-sm">
                <div className="flex justify-between">
                  <span className="text-muted-foreground">Signed in as</span>
                  <span className="font-medium">{user.email}</span>
                </div>
              </div>
            </div>
            <div className="flex flex-col gap-2">
              <Button
                className="w-full"
                onClick={() => {
                  const redirectUrl = returnUrl
                    ? `/sign-in?returnUrl=${encodeURIComponent(returnUrl)}`
                    : "/sign-in"
                  logout(redirectUrl)
                }}
              >
                Sign in with a different account
              </Button>
              <Button asChild variant="ghost" className="w-full">
                <Link href="/workspaces">Go to workspaces</Link>
              </Button>
            </div>
          </CardContent>
        </div>
      </div>
    )
  }

  return (
    <div
      className={cn(
        "container flex size-full items-center justify-center",
        className
      )}
    >
      <div className="flex w-full flex-1 flex-col justify-center gap-2 px-8 sm:max-w-md">
        <CardHeader className="items-center space-y-2 text-center">
          <Image src={TracecatIcon} alt="Tracecat" className="mb-8 size-16" />
          <CardTitle className="text-2xl">Create an account</CardTitle>
          <CardDescription>
            Enter your email below to create an account
          </CardDescription>
        </CardHeader>
        <CardContent>
          <div className="grid gap-4">
            <BasicRegistrationForm
              returnUrl={returnUrl}
              onInvitationEmailMismatch={() => setInvitationEmailMismatch(true)}
              onInvitationAccepted={setInvitationRedirectUrl}
              onInvitationProcessingChange={setIsProcessingInvitation}
            />
          </div>
          <div className="mt-4 text-center text-sm text-muted-foreground">
            Already have an account?{" "}
            <Link
              href={
                returnUrl
                  ? `/sign-in?returnUrl=${encodeURIComponent(returnUrl)}`
                  : "/sign-in"
              }
              className="underline"
            >
              Sign in
            </Link>
          </div>
        </CardContent>
      </div>
    </div>
  )
}

const basicRegistrationSchema = z.object({
  email: z.string().email().min(3, { message: "Required" }),
  password: z
    .string()
    .min(12, "Password needs to be at least 12 characters long"),
})
type BasicLoginForm = z.infer<typeof basicRegistrationSchema>

interface BasicRegistrationFormProps {
  returnUrl?: string | null
  onInvitationEmailMismatch?: () => void
  onInvitationAccepted?: (redirectUrl: string) => void
  onInvitationProcessingChange?: (isProcessing: boolean) => void
}

/**
 * Extract invitation token and type from a returnUrl if it's an invitation accept URL.
 */
function extractInvitation(
  returnUrl: string | null
): ExtractedInvitation | null {
  if (!returnUrl) return null
  try {
    const url = new URL(returnUrl, window.location.origin)
    const token = url.searchParams.get("token")
    if (!token) return null

    if (url.pathname === "/invitations/accept") {
      return { type: "organization", token }
    }
    if (url.pathname === "/invitations/workspace/accept") {
      return { type: "workspace", token }
    }
  } catch {
    // Invalid URL, ignore
  }
  return null
}

/**
 * Extract invitation token from a returnUrl if it's an invitation accept URL.
 */
function extractInvitationToken(returnUrl: string | null): string | null {
  if (!returnUrl) return null
  try {
    // returnUrl might be like "/invitations/accept?token=abc123"
    const url = new URL(returnUrl, window.location.origin)
    if (url.pathname === "/invitations/accept") {
      return url.searchParams.get("token")
    }
  } catch {
    // Invalid URL, ignore
  }
  return null
}

export function BasicRegistrationForm({
  returnUrl,
  onInvitationEmailMismatch,
  onInvitationAccepted,
  onInvitationProcessingChange,
}: BasicRegistrationFormProps) {
  const [isLoading, setIsLoading] = useState(false)
  const { register, login } = useAuthActions()
  const form = useForm<BasicLoginForm>({
    resolver: zodResolver(basicRegistrationSchema),
    defaultValues: {
      email: "",
      password: "",
    },
  })

  const onSubmit = async (values: BasicLoginForm) => {
    try {
      setIsLoading(true)

      // Extract invitation token to pass during registration
      // This enables atomic invitation acceptance during registration
      const invitationToken = extractInvitationToken(returnUrl ?? null)

      /**
       * XXX(auth): The user cannot set is_active or is_superuser itself at registration.
       * Only a superuser can do it by PATCHing the user.
       */
      await register({
        requestBody: {
          email: values.email,
          password: values.password,
          ...(invitationToken && { invitation_token: invitationToken }),
        },
      })

      // On successful registration, log the user in
      await login({
        formData: {
          username: values.email,
          password: values.password,
        },
      })

      // After successful login, check if this is an invitation flow
      const invitation = extractInvitation(returnUrl ?? null)
      if (invitation) {
        onInvitationProcessingChange?.(true)
        try {
          if (invitation.type === "workspace") {
            // Workspace invitation: check email match, then auto-accept
            const wsInvitation = await workspacesGetInvitationByToken({
              token: invitation.token,
            })
            if (wsInvitation.email_matches === false) {
              onInvitationEmailMismatch?.()
              return
            }
            // Auto-accept and redirect to workspace
            await workspacesAcceptWorkspaceInvitation({
              requestBody: { token: invitation.token },
            })
            onInvitationAccepted?.(`/workspaces/${wsInvitation.workspace_id}`)
          } else {
            // Organization invitation: check email match, then auto-accept
            const orgInvitation = await organizationGetInvitationByToken({
              token: invitation.token,
            })
            if (orgInvitation.email_matches === false) {
              onInvitationEmailMismatch?.()
              return
            }
            // Auto-accept and redirect to workspaces
            await organizationAcceptInvitation({
              requestBody: { token: invitation.token },
            })
            onInvitationAccepted?.("/workspaces")
          }
        } catch {
          // Failed to fetch/accept invitation, let the redirect happen and handle error there
          console.error("Failed to process invitation")
        } finally {
          onInvitationProcessingChange?.(false)
        }
      }
    } catch (error) {
      if (error instanceof ApiError) {
        const apiError = error as TracecatApiError

        // Handle both string and object error details
        if (typeof apiError.body.detail === "string") {
          switch (apiError.body.detail) {
            case "REGISTER_USER_ALREADY_EXISTS":
              form.setError("email", {
                message: "User already exists",
              })
              break
            default:
              form.setError("email", {
                message: String(apiError.body.detail),
              })
          }
        } else if (
          typeof apiError.body.detail === "object" &&
          apiError.body.detail !== null
        ) {
          const detail = apiError.body.detail as
            | EmailLoginValidationError
            | RequestValidationError[]

          if (isEmailLoginValidationError(detail)) {
            switch (detail.code) {
              case "REGISTER_INVALID_PASSWORD":
                form.setError("password", {
                  message: detail.reason,
                })
                break
              default:
                console.error("Unknown email validation error", detail)
                form.setError("email", {
                  message: detail.reason,
                })
            }
          } else {
            // Handle RequestValidationError case
            form.setError("email", {
              message: detail[0].msg || "Unknown error",
            })
          }
        }
      } else {
        console.error("Error registering user", error)
        throw error
      }
    } finally {
      setIsLoading(false)
    }
  }
  return (
    <Form {...form}>
      <form onSubmit={form.handleSubmit(onSubmit)} className="space-y-8">
        <div className="flex w-full flex-col space-y-4">
          <div className="grid gap-4">
            <div className="grid gap-2">
              <FormField
                control={form.control}
                name="email"
                render={({ field }) => (
                  <FormItem>
                    <FormLabel className="text-xs">Email</FormLabel>
                    <FormControl>
                      <Input placeholder="user@company.com" {...field} />
                    </FormControl>
                    <FormMessage />
                  </FormItem>
                )}
              />
            </div>
            <div className="grid gap-2">
              <div className="grid gap-2">
                <FormField
                  control={form.control}
                  name="password"
                  render={({ field }) => (
                    <FormItem>
                      <FormLabel className="text-xs">Password</FormLabel>
                      <FormControl>
                        <Input
                          type="password"
                          placeholder="••••••••••••••••"
                          {...field}
                        />
                      </FormControl>
                      <FormMessage />
                    </FormItem>
                  )}
                />
              </div>
            </div>
          </div>
          <Button className="w-full text-sm" disabled={isLoading} type="submit">
            {isLoading && (
              <Icons.spinner className="mr-2 size-4 animate-spin" />
            )}
            Create account
          </Button>
        </div>
      </form>
    </Form>
  )
}
