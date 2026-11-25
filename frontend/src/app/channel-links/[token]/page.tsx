"use client"

import { AlertCircle, Check, X } from "lucide-react"
import Image from "next/image"
import Link from "next/link"
import { useParams, useRouter, useSearchParams } from "next/navigation"
import TracecatIcon from "public/icon.png"
import { Suspense, useEffect, useRef } from "react"
import { agentChannelUsersStartChannelLinkSignIn } from "@/client"
import {
  ChannelProviderIcon,
  channelProviderName,
} from "@/components/agents/channel-provider"
import { CenteredSpinner } from "@/components/loading/spinner"
import { Alert, AlertDescription } from "@/components/ui/alert"
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
import { LINK_FAILURES, type LinkFailure } from "@/lib/channel-link-failures"
import { getApiErrorDetail, type TracecatApiError } from "@/lib/errors"
import { useMutation } from "@/lib/query"
import { cn } from "@/lib/utils"

function linkFailure(
  result: string | null | undefined,
  errorCode: string,
  apiError: Error | null
): LinkFailure | null {
  if (result === "error") {
    return Object.hasOwn(LINK_FAILURES, errorCode)
      ? LINK_FAILURES[errorCode]
      : LINK_FAILURES.invalid
  }
  if (apiError) {
    return {
      message:
        getApiErrorDetail(apiError) ?? "This link is invalid or has expired.",
      retryable: false,
    }
  }
  return null
}

type LinkOrigin = {
  provider: string
  tenant: string
}

function ProviderLinkHeader({
  provider,
  linked,
}: {
  provider: string
  linked: boolean
}) {
  const Mark = linked ? Check : X
  return (
    <div className="mb-4 flex items-center gap-2.5" aria-hidden="true">
      <div className="flex size-12 items-center justify-center rounded-xl border bg-white">
        <ChannelProviderIcon provider={provider} className="size-6" />
      </div>
      <div className="relative h-0.5 w-11 bg-border">
        <span
          className={cn(
            "absolute left-1/2 top-1/2 flex size-5 -translate-x-1/2 -translate-y-1/2 items-center justify-center rounded-full border-[1.5px] bg-background",
            linked
              ? "border-muted-foreground/60 text-foreground"
              : "border-border text-muted-foreground"
          )}
        >
          <Mark className="size-3" strokeWidth={2.5} />
        </span>
      </div>
      <Image src={TracecatIcon} alt="" className="size-12" />
    </div>
  )
}

function LinkFooter({ provider, tenant }: LinkOrigin) {
  // Only Slack has a deep link that opens a tenant.
  const returnUrl =
    provider === "slack" && tenant
      ? `slack://open?team=${encodeURIComponent(tenant)}`
      : null
  return (
    <CardFooter className="flex-col gap-2">
      {returnUrl && (
        <Button asChild className="w-full">
          <a href={returnUrl}>Back to {channelProviderName(provider)}</a>
        </Button>
      )}
      <Button
        asChild
        variant={returnUrl ? "ghost" : "outline"}
        className="w-full"
      >
        <Link href="/workspaces">Go to Tracecat</Link>
      </Button>
    </CardFooter>
  )
}

function LinkOutcomeCard({
  provider,
  tenant,
  email,
  failure,
}: LinkOrigin & { email?: string; failure?: LinkFailure }) {
  const name = channelProviderName(provider)
  const place = name ?? "the chat"
  let title = name ? `${name} account linked` : "Account linked"
  let description = `Your ${name ? `${name} account` : "account"} is now linked to Tracecat.`
  if (failure) {
    title = name
      ? `Couldn't link your ${name} account`
      : "Couldn't link your account"
    description = failure.retryable
      ? `Open the link in ${place} again to retry.`
      : `Ask the agent again in ${place} for a new link.`
  }
  return (
    <Card className="w-full max-w-md">
      <CardHeader className="items-center text-center">
        <ProviderLinkHeader provider={provider} linked={!failure} />
        <CardTitle>{title}</CardTitle>
        <CardDescription>{description}</CardDescription>
      </CardHeader>
      {failure ? (
        <CardContent>
          <Alert variant="destructive">
            <AlertCircle className="size-4" />
            <AlertDescription>{failure.message}</AlertDescription>
          </Alert>
        </CardContent>
      ) : (
        email && (
          <CardContent>
            <div className="flex justify-between gap-3 rounded-lg border bg-muted/50 p-4 text-sm">
              <span className="text-muted-foreground">Tracecat account</span>
              <span className="min-w-0 truncate font-medium">{email}</span>
            </div>
          </CardContent>
        )
      )}
      <LinkFooter provider={provider} tenant={tenant} />
    </Card>
  )
}

function ChannelLinkContent() {
  const { token } = useParams<{ token: string }>()
  const searchParams = useSearchParams()
  const result = searchParams?.get("result")
  const router = useRouter()
  const { user, userIsLoading } = useAuth()
  const signInStarted = useRef(false)
  const signIn = useMutation<{ sign_in_url: string }, TracecatApiError>({
    mutationFn: async () =>
      await agentChannelUsersStartChannelLinkSignIn({ token }),
    onSuccess: ({ sign_in_url }) => window.location.assign(sign_in_url),
  })
  const { mutate: startSignIn } = signIn

  useEffect(() => {
    if (userIsLoading || result) return
    if (!user) {
      // A callback outcome shows as is, including a signed_out error.
      const returnUrl = `/channel-links/${encodeURIComponent(token)}`
      router.replace(`/sign-in?returnUrl=${encodeURIComponent(returnUrl)}`)
      return
    }
    // Strict mode runs effects twice; one start mints one sign-in state.
    if (signInStarted.current) return
    signInStarted.current = true
    startSignIn()
  }, [user, userIsLoading, token, router, result, startSignIn])

  const provider = searchParams?.get("provider") ?? ""
  const tenant = searchParams?.get("tenant") ?? ""
  if (result === "linked") {
    return (
      <LinkOutcomeCard
        provider={provider}
        tenant={tenant}
        email={user?.email}
      />
    )
  }
  const failure = linkFailure(
    result,
    searchParams?.get("error") ?? "",
    signIn.error
  )
  if (failure) {
    return (
      <LinkOutcomeCard provider={provider} tenant={tenant} failure={failure} />
    )
  }
  return <CenteredSpinner />
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
