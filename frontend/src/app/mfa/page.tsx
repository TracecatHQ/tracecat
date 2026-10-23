"use client"

import { KeyRound } from "lucide-react"
import { useRouter, useSearchParams } from "next/navigation"
import { Suspense, useEffect, useState } from "react"
import { authSignOutPendingSession, authVerifyRecoveryCode } from "@/client"
import { RecoveryCodes } from "@/components/auth/recovery-codes"
import { CenteredSpinner } from "@/components/loading/spinner"
import { Button } from "@/components/ui/button"
import {
  Card,
  CardContent,
  CardDescription,
  CardHeader,
  CardTitle,
} from "@/components/ui/card"
import { Input } from "@/components/ui/input"
import { getPostAuthRedirectPath } from "@/lib/auth-redirect"
import { sanitizeReturnUrl } from "@/lib/auth-return-url"
import { getApiErrorDetail } from "@/lib/errors"
import { useQuery, useQueryClient } from "@/lib/query"
import {
  getMfaStatus,
  isMfaPending,
  MFA_STATUS_QUERY_KEY,
  registerSecurityKey,
  verifyWithSecurityKey,
} from "@/lib/webauthn"

function errorMessage(error: unknown): string {
  return (
    getApiErrorDetail(error) ??
    (error instanceof Error ? error.message : "Please try again.")
  )
}

function MfaContent() {
  const router = useRouter()
  const searchParams = useSearchParams()
  const returnUrl = sanitizeReturnUrl(searchParams?.get("returnUrl") ?? null)
  const queryClient = useQueryClient()
  const {
    data: status,
    isLoading,
    isError,
    refetch,
  } = useQuery({
    queryKey: MFA_STATUS_QUERY_KEY,
    queryFn: getMfaStatus,
    retry: false,
  })
  const [keyName, setKeyName] = useState("Security key")
  const [recoveryCode, setRecoveryCode] = useState("")
  const [useRecoveryCode, setUseRecoveryCode] = useState(false)
  const [recoveryCodes, setRecoveryCodes] = useState<string[] | null>(null)
  const [busy, setBusy] = useState(false)
  const [error, setError] = useState<string | null>(null)

  const finish = async () => {
    await queryClient.invalidateQueries({ queryKey: ["auth"] })
    router.replace(getPostAuthRedirectPath({ returnUrl }))
  }

  useEffect(() => {
    if (isLoading || recoveryCodes) {
      return
    }
    if (status === null) {
      router.replace("/sign-in")
    } else if (status && !isMfaPending(status.state)) {
      void finish()
    }
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [status, isLoading, recoveryCodes])

  const run = async (action: () => Promise<void>) => {
    setBusy(true)
    setError(null)
    try {
      await action()
    } catch (e) {
      setError(errorMessage(e))
    } finally {
      setBusy(false)
    }
  }

  const signOut = async () => {
    await authSignOutPendingSession()
    await queryClient.invalidateQueries({ queryKey: ["auth"] })
    router.replace("/sign-in")
  }

  if (isError) {
    return (
      <div className="flex size-full items-center justify-center p-4">
        <Card className="w-full max-w-md">
          <CardHeader>
            <CardTitle>Couldn&apos;t load your sign-in status</CardTitle>
            <CardDescription>
              Check your connection and try again.
            </CardDescription>
          </CardHeader>
          <CardContent className="flex justify-between text-sm">
            <Button
              variant="link"
              className="h-auto p-0"
              onClick={() => void refetch()}
            >
              Try again
            </Button>
            <Button variant="link" className="h-auto p-0" onClick={signOut}>
              Sign out
            </Button>
          </CardContent>
        </Card>
      </div>
    )
  }

  if (isLoading || !status) {
    return <CenteredSpinner />
  }

  const enrolling = status.state === "enrollment_required" || recoveryCodes
  return (
    <div className="flex size-full items-center justify-center p-4">
      <Card className="w-full max-w-md">
        <CardHeader>
          <CardTitle className="flex items-center gap-2">
            <KeyRound className="size-5" />
            {enrolling
              ? "Set up a security key"
              : "Verify with your security key"}
          </CardTitle>
          <CardDescription>
            {enrolling
              ? "Your organization requires a security key or passkey to sign in. Register a hardware key (such as a YubiKey) or a passkey from your browser or password manager."
              : "Use the security key or passkey registered to your account to continue."}
          </CardDescription>
        </CardHeader>
        <CardContent className="space-y-4">
          {!status.webauthn_available && (
            <p className="text-sm text-destructive">
              {status.webauthn_unavailable_reason}
            </p>
          )}
          {recoveryCodes ? (
            <>
              <RecoveryCodes codes={recoveryCodes} />
              <Button className="w-full" onClick={finish}>
                Continue
              </Button>
            </>
          ) : enrolling ? (
            <>
              <Input
                aria-label="Security key name"
                value={keyName}
                maxLength={100}
                onChange={(e) => setKeyName(e.target.value)}
              />
              <Button
                className="w-full"
                disabled={busy || !keyName.trim() || !status.webauthn_available}
                onClick={() =>
                  run(async () => {
                    const result = await registerSecurityKey(keyName.trim())
                    if (result.recovery_codes?.length) {
                      setRecoveryCodes(result.recovery_codes)
                    } else {
                      await finish()
                    }
                  })
                }
              >
                {busy ? "Waiting for security key..." : "Register security key"}
              </Button>
            </>
          ) : useRecoveryCode ? (
            <form
              className="space-y-3"
              onSubmit={(e) => {
                e.preventDefault()
                void run(async () => {
                  await authVerifyRecoveryCode({
                    requestBody: { code: recoveryCode.trim() },
                  })
                  await finish()
                })
              }}
            >
              <Input
                aria-label="Recovery code"
                placeholder="xxxxx-xxxxx-xxxxx-xxxxx-xxxxx"
                autoComplete="one-time-code"
                value={recoveryCode}
                onChange={(e) => setRecoveryCode(e.target.value)}
              />
              <Button
                type="submit"
                className="w-full"
                disabled={busy || !recoveryCode.trim()}
              >
                Verify recovery code
              </Button>
            </form>
          ) : (
            <Button
              className="w-full"
              disabled={busy || !status.webauthn_available}
              onClick={() =>
                run(async () => {
                  await verifyWithSecurityKey()
                  await finish()
                })
              }
            >
              {busy ? "Waiting for security key..." : "Use security key"}
            </Button>
          )}
          {error && <p className="text-sm text-destructive">{error}</p>}
          <div className="flex justify-between text-sm">
            {status.state === "challenge_required" && !recoveryCodes ? (
              <Button
                variant="link"
                className="h-auto p-0"
                onClick={() => {
                  setError(null)
                  setUseRecoveryCode(!useRecoveryCode)
                }}
              >
                {useRecoveryCode ? "Use security key" : "Use a recovery code"}
              </Button>
            ) : (
              <span />
            )}
            {!recoveryCodes && (
              <Button variant="link" className="h-auto p-0" onClick={signOut}>
                Sign out
              </Button>
            )}
          </div>
        </CardContent>
      </Card>
    </div>
  )
}

export default function MfaPage() {
  return (
    <Suspense fallback={<CenteredSpinner />}>
      <MfaContent />
    </Suspense>
  )
}
