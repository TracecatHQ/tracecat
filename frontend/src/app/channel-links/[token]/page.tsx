"use client"

import { AlertCircle, CheckCircle2, Link2 } from "lucide-react"
import Link from "next/link"
import { useParams, useRouter, useSearchParams } from "next/navigation"
import { type ReactNode, Suspense, useEffect } from "react"
import {
  agentChannelUsersConfirmChannelLink,
  agentChannelUsersGetChannelLink,
} from "@/client"
import { CenteredSpinner } from "@/components/loading/spinner"
import { Button } from "@/components/ui/button"
import {
  Card,
  CardDescription,
  CardFooter,
  CardHeader,
  CardTitle,
} from "@/components/ui/card"
import { useAuth } from "@/hooks/use-auth"
import { getApiErrorDetail, type TracecatApiError } from "@/lib/errors"
import { useMutation, useQuery } from "@/lib/query"

// The callback redirects with a code, never text, so the page shows only this copy.
const LINK_ERRORS: Record<string, string> = {
  invalid: "This link is invalid, expired, or already used.",
  not_member: "Sign in as a member of the organization that sent this link.",
  other_user: "Sign in as the Tracecat user who started linking.",
  wrong_account:
    "You signed in to the provider as a different user than the one who asked to link.",
  cancelled: "Sign-in was cancelled.",
  signed_out: "Your Tracecat session ended. Open the link again to retry.",
  failed: "The provider could not confirm your sign-in.",
}

function linkErrorMessage(
  result: string | null | undefined,
  errorCode: string,
  apiError: Error | null
): string | null {
  if (result === "error") {
    return Object.hasOwn(LINK_ERRORS, errorCode)
      ? LINK_ERRORS[errorCode]
      : LINK_ERRORS.invalid
  }
  if (apiError) {
    return getApiErrorDetail(apiError) ?? "This link is invalid or has expired."
  }
  return null
}

function Outcome({
  icon,
  title,
  description,
  footer,
}: {
  icon: ReactNode
  title: string
  description: string
  footer?: ReactNode
}) {
  return (
    <Card className="w-full max-w-md">
      <CardHeader className="items-center text-center">
        {icon}
        <CardTitle>{title}</CardTitle>
        <CardDescription>{description}</CardDescription>
      </CardHeader>
      <CardFooter className="justify-center gap-2">
        {footer ?? (
          <Button asChild variant="outline">
            <Link href="/workspaces">Go to Tracecat</Link>
          </Button>
        )}
      </CardFooter>
    </Card>
  )
}

function ChannelLinkContent() {
  const { token } = useParams<{ token: string }>()
  const searchParams = useSearchParams()
  const result = searchParams?.get("result")
  const router = useRouter()
  const { user, userIsLoading } = useAuth()

  useEffect(() => {
    // A callback outcome shows as is; its signed_out error says to reopen the link.
    if (!userIsLoading && !user && !result) {
      const returnUrl = `/channel-links/${encodeURIComponent(token)}`
      router.replace(`/sign-in?returnUrl=${encodeURIComponent(returnUrl)}`)
    }
  }, [user, userIsLoading, token, router, result])

  const details = useQuery({
    queryKey: ["channel-link", token],
    queryFn: async () => await agentChannelUsersGetChannelLink({ token }),
    enabled: Boolean(user) && !result,
    retry: false,
  })
  const confirm = useMutation<{ sign_in_url: string }, TracecatApiError>({
    mutationFn: async () =>
      await agentChannelUsersConfirmChannelLink({ token }),
    onSuccess: ({ sign_in_url }) => window.location.assign(sign_in_url),
  })

  if (result === "linked") {
    return (
      <Outcome
        icon={<CheckCircle2 className="mb-4 size-12 text-green-500" />}
        title="Account linked"
        description="Return to the channel and try again."
      />
    )
  }
  const error = linkErrorMessage(
    result,
    searchParams?.get("error") ?? "",
    details.error ?? confirm.error
  )
  if (error) {
    return (
      <Outcome
        icon={<AlertCircle className="mb-4 size-12 text-destructive" />}
        title="Account not linked"
        description={error}
      />
    )
  }
  if (!user || !details.data) {
    return <CenteredSpinner />
  }
  const { provider, provider_user_id, provider_tenant_id, user_email } =
    details.data
  return (
    <Outcome
      icon={<Link2 className="mb-4 size-12 text-muted-foreground" />}
      title={`Link your ${provider} account`}
      description={`Link ${provider} user ${provider_user_id} in ${provider_tenant_id} to ${user_email}? You'll sign in to ${provider} to confirm.`}
      footer={
        <>
          <Button
            onClick={() => confirm.mutate()}
            disabled={confirm.isPending || confirm.isSuccess}
          >
            Continue
          </Button>
          <Button asChild variant="ghost">
            <Link href="/workspaces">Cancel</Link>
          </Button>
        </>
      }
    />
  )
}

export default function ChannelLinkPage() {
  return (
    <div className="flex min-h-screen items-center justify-center p-4">
      <Suspense fallback={<CenteredSpinner />}>
        <ChannelLinkContent />
      </Suspense>
    </div>
  )
}
