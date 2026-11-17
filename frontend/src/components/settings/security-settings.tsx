"use client"

import { KeyRound, Trash2 } from "lucide-react"
import { useState } from "react"
import { RecoveryCodes } from "@/components/auth/recovery-codes"
import { CenteredSpinner } from "@/components/loading/spinner"
import { Badge } from "@/components/ui/badge"
import { Button } from "@/components/ui/button"
import { Input } from "@/components/ui/input"
import { toast } from "@/components/ui/use-toast"
import { useSecurityKeys } from "@/hooks/use-mfa"
import { getApiErrorDetail } from "@/lib/errors"
import { useQuery } from "@/lib/query"
import {
  getMfaStatus,
  MFA_STATUS_QUERY_KEY,
  registerSecurityKey,
} from "@/lib/webauthn"

function formatDate(value: string | null) {
  return value ? new Date(value).toLocaleDateString() : "Never"
}

export function SecuritySettings() {
  const { data: status } = useQuery({
    queryKey: MFA_STATUS_QUERY_KEY,
    queryFn: getMfaStatus,
  })
  const {
    credentials,
    isLoading,
    refresh,
    deleteCredential,
    deletePending,
    regenerateRecoveryCodes,
    regeneratePending,
  } = useSecurityKeys()
  const [name, setName] = useState("")
  const [registering, setRegistering] = useState(false)
  const [recoveryCodes, setRecoveryCodes] = useState<string[] | null>(null)

  const addKey = async () => {
    setRegistering(true)
    try {
      const result = await registerSecurityKey(name.trim() || "Security key")
      setName("")
      if (result.recovery_codes?.length) {
        setRecoveryCodes(result.recovery_codes)
      }
      await refresh()
      toast({ title: "Security key added" })
    } catch (error) {
      toast({
        title: "Failed to add security key",
        description:
          getApiErrorDetail(error) ??
          (error instanceof Error ? error.message : "Please try again."),
        variant: "destructive",
      })
    } finally {
      setRegistering(false)
    }
  }

  if (isLoading) {
    return <CenteredSpinner />
  }

  const hasKeys = (credentials?.length ?? 0) > 0
  return (
    <div className="space-y-8">
      <div className="space-y-1">
        <h3 className="text-lg font-semibold">Security keys and passkeys</h3>
        <p className="text-sm text-muted-foreground">
          Sign in with a hardware security key (such as a YubiKey) or a passkey
          from your browser or password manager. Once added, you&apos;ll need it
          every time you sign in.
          {status?.required_by_policy &&
            " Your organization requires at least one."}
        </p>
        {status && !status.webauthn_available && (
          <p className="text-sm text-destructive">
            {status.webauthn_unavailable_reason}
          </p>
        )}
      </div>

      <div className="divide-y rounded-md border">
        {hasKeys ? (
          credentials?.map((credential) => (
            <div
              key={credential.id}
              className="flex items-center justify-between gap-4 p-3"
            >
              <div className="flex min-w-0 items-center gap-3">
                <KeyRound className="size-4 shrink-0 text-muted-foreground" />
                <div className="min-w-0">
                  <div className="flex items-center gap-2">
                    <span className="truncate text-sm font-medium">
                      {credential.name}
                    </span>
                    <Badge variant="secondary">
                      {credential.backup_eligible
                        ? "Synced passkey"
                        : "Security key"}
                    </Badge>
                  </div>
                  <p className="text-xs text-muted-foreground">
                    Added {formatDate(credential.created_at)} · Last used{" "}
                    {formatDate(credential.last_used_at)}
                  </p>
                </div>
              </div>
              <Button
                variant="ghost"
                size="icon"
                aria-label={`Remove ${credential.name}`}
                disabled={deletePending}
                onClick={() =>
                  void deleteCredential(credential.id).catch(() => undefined)
                }
              >
                <Trash2 className="size-4" />
              </Button>
            </div>
          ))
        ) : (
          <p className="p-3 text-sm text-muted-foreground">
            No security keys yet.
          </p>
        )}
      </div>

      <div className="flex gap-2">
        <Input
          aria-label="New security key name"
          placeholder="Name, e.g. YubiKey 5C"
          value={name}
          maxLength={100}
          onChange={(e) => setName(e.target.value)}
        />
        <Button
          onClick={addKey}
          disabled={registering || status?.webauthn_available === false}
        >
          {registering ? "Waiting for key..." : "Add security key"}
        </Button>
      </div>

      {hasKeys && (
        <div className="space-y-3">
          <div className="space-y-1">
            <h3 className="text-lg font-semibold">Recovery codes</h3>
            <p className="text-sm text-muted-foreground">
              {status?.recovery_codes_remaining ?? 0} unused. Generating new
              codes invalidates the old ones.
            </p>
          </div>
          {recoveryCodes && <RecoveryCodes codes={recoveryCodes} />}
          <Button
            variant="outline"
            disabled={regeneratePending}
            onClick={async () => {
              try {
                setRecoveryCodes(await regenerateRecoveryCodes())
              } catch {
                // Toast is shown by the mutation.
              }
            }}
          >
            Generate new recovery codes
          </Button>
        </div>
      )}
    </div>
  )
}
