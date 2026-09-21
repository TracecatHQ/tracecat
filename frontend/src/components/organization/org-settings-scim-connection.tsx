"use client"

import { KeyRoundIcon, Loader2 } from "lucide-react"
import { useEffect, useState } from "react"
import type { ScimConnectionRead } from "@/client"
import { useScopeCheck } from "@/components/auth/scope-guard"
import { ConfirmDestructiveDialog } from "@/components/confirm-destructive-dialog"
import { CopyButton } from "@/components/copy-button"
import { CenteredSpinner } from "@/components/loading/spinner"
import { Alert, AlertDescription, AlertTitle } from "@/components/ui/alert"
import { Button } from "@/components/ui/button"
import {
  Dialog,
  DialogContent,
  DialogDescription,
  DialogFooter,
  DialogHeader,
  DialogTitle,
} from "@/components/ui/dialog"
import {
  Empty,
  EmptyContent,
  EmptyDescription,
  EmptyHeader,
  EmptyMedia,
  EmptyTitle,
} from "@/components/ui/empty"
import { useScimConnection } from "@/hooks/use-scim"
import { getBaseUrl } from "@/lib/api"

/** Render a nullable ISO timestamp as a readable local time. */
function formatTimestamp(value: string | null | undefined): string {
  if (!value) {
    return "Never"
  }
  return new Date(value).toLocaleString()
}

/**
 * Absolute SCIM base URL an administrator pastes into their IdP.
 *
 * Resolved after mount: `getBaseUrl()` returns the internal server address
 * during prerender, which is unreachable from the administrator's browser.
 */
function useScimBaseUrl(): string | null {
  const [baseUrl, setBaseUrl] = useState<string | null>(null)
  useEffect(() => {
    const path = `${getBaseUrl().replace(/\/$/, "")}/scim/v2`
    setBaseUrl(new URL(path, window.location.origin).href)
  }, [])
  return baseUrl
}

function ConnectionDetails({ connection }: { connection: ScimConnectionRead }) {
  const baseUrl = useScimBaseUrl()

  return (
    <dl className="grid grid-cols-[160px_1fr] gap-x-6 gap-y-3 text-sm">
      <dt className="text-muted-foreground">SCIM base URL</dt>
      <dd className="flex min-w-0 items-center gap-2">
        <code className="min-w-0 truncate font-mono text-foreground/90 select-all">
          {baseUrl ?? "Loading…"}
        </code>
        {baseUrl && (
          <CopyButton
            value={baseUrl}
            toastMessage="SCIM base URL copied"
            tooltipMessage="Copy base URL"
          />
        )}
      </dd>

      <dt className="text-muted-foreground">Token</dt>
      <dd className="font-mono text-foreground/90">{connection.preview}</dd>

      <dt className="text-muted-foreground">Last used</dt>
      <dd>{formatTimestamp(connection.last_used_at)}</dd>

      <dt className="text-muted-foreground">Provisioning status</dt>
      <dd>
        {
          {
            active: "Active",
            pending: "Pending activation",
            disabled: "Disabled",
          }[connection.status]
        }
      </dd>
      <dt className="text-muted-foreground">Token status</dt>
      <dd>
        {connection.revoked_at
          ? `Revoked ${formatTimestamp(connection.revoked_at)}`
          : "Valid"}
      </dd>
    </dl>
  )
}

/**
 * Issue, rotate, and revoke the SCIM connection token.
 *
 * The raw token is held only in local component state for the lifetime of the
 * dialog that displays it. It is never written to the query cache or refetched,
 * because the API returns it exactly once and can never return it again.
 */
export function OrgSettingsScimConnection() {
  const canManage = useScopeCheck("org:scim:manage")
  const {
    connection,
    connectionIsLoading,
    connectionIsFetching,
    connectionError,
    refetchConnection,
    issueToken,
    issueTokenIsPending,
    revokeToken,
    revokeTokenIsPending,
  } = useScimConnection()

  const [issuedToken, setIssuedToken] = useState<string | null>(null)
  const [rotateOpen, setRotateOpen] = useState(false)
  const [revokeOpen, setRevokeOpen] = useState(false)

  async function handleIssue() {
    const issued = await issueToken()
    setIssuedToken(issued.token)
  }

  async function handleRotate() {
    await handleIssue()
    setRotateOpen(false)
  }

  async function handleRevoke() {
    await revokeToken()
    setRevokeOpen(false)
  }

  if (connectionIsLoading) {
    return <CenteredSpinner />
  }

  const isActive = Boolean(connection) && !connection?.revoked_at

  return (
    <div className="space-y-4">
      <div className="space-y-1">
        <h3 className="text-lg font-medium">Connection</h3>
        <p className="text-sm text-muted-foreground">
          Your identity provider authenticates to Tracecat with this token.
        </p>
        <p className="text-sm text-muted-foreground">
          SCIM username and email changes are not supported yet. Updates that
          change an existing user&apos;s login address are rejected.
        </p>
      </div>

      {connectionError && (
        <Alert>
          <AlertTitle>Could not load SCIM connection</AlertTitle>
          <AlertDescription>
            <p>
              Retry to check your existing connection before making changes.
            </p>
            <Button
              variant="outline"
              className="mt-3"
              disabled={connectionIsFetching}
              onClick={() => void refetchConnection()}
            >
              Retry
            </Button>
          </AlertDescription>
        </Alert>
      )}

      {!connectionError && connection && (
        <div className="space-y-6 rounded-lg border p-6">
          <ConnectionDetails connection={connection} />
          <div className="flex gap-2">
            <Button
              variant="outline"
              onClick={() => setRotateOpen(true)}
              disabled={canManage !== true || issueTokenIsPending}
            >
              {issueTokenIsPending ? (
                <Loader2 className="mr-2 size-4 animate-spin" />
              ) : null}
              Rotate token
            </Button>
            {isActive ? (
              <Button
                variant="outline"
                onClick={() => setRevokeOpen(true)}
                disabled={canManage !== true || revokeTokenIsPending}
              >
                Revoke token
              </Button>
            ) : null}
          </div>
        </div>
      )}

      {!connectionError && !connection && (
        <Empty className="gap-4 rounded-lg border py-12">
          <EmptyHeader>
            <EmptyMedia variant="icon">
              <KeyRoundIcon className="size-6" />
            </EmptyMedia>
            <EmptyTitle>SCIM is not configured</EmptyTitle>
            <EmptyDescription>
              Generate a token, then paste it along with the SCIM base URL into
              your identity provider to start provisioning users and groups.
            </EmptyDescription>
          </EmptyHeader>
          <EmptyContent>
            <Button
              onClick={handleIssue}
              disabled={canManage !== true || issueTokenIsPending}
            >
              {issueTokenIsPending ? (
                <Loader2 className="mr-2 size-4 animate-spin" />
              ) : null}
              Generate token
            </Button>
          </EmptyContent>
        </Empty>
      )}

      <Dialog
        open={Boolean(issuedToken)}
        onOpenChange={(open) => {
          if (!open) {
            setIssuedToken(null)
          }
        }}
      >
        <DialogContent className="max-w-md">
          <DialogHeader>
            <DialogTitle>Copy SCIM token</DialogTitle>
            <DialogDescription>
              This token is only shown once. Copy it now before closing this
              dialog — it cannot be retrieved again, only rotated.
            </DialogDescription>
          </DialogHeader>
          {issuedToken ? (
            <div className="flex min-w-0 max-w-full items-center gap-3 rounded-lg bg-muted/60 px-4 py-3.5">
              <code className="min-w-0 flex-1 truncate font-mono text-sm text-foreground/90 select-all">
                {issuedToken}
              </code>
              <CopyButton
                value={issuedToken}
                toastMessage="SCIM token copied"
                tooltipMessage="Copy token"
                className="size-6 shrink-0"
                iconClassName="size-4 text-foreground/70"
              />
            </div>
          ) : null}
          <DialogFooter>
            <Button variant="outline" onClick={() => setIssuedToken(null)}>
              Close
            </Button>
          </DialogFooter>
        </DialogContent>
      </Dialog>

      <ConfirmDestructiveDialog
        open={rotateOpen}
        onOpenChange={setRotateOpen}
        confirmPhrase="rotate"
        title="Rotate SCIM token"
        description="The current token stops working immediately. Provisioning fails until you paste the new token into your identity provider."
        confirmLabel="Rotate token"
        isPending={issueTokenIsPending}
        onConfirm={handleRotate}
      />

      <ConfirmDestructiveDialog
        open={revokeOpen}
        onOpenChange={setRevokeOpen}
        confirmPhrase="revoke"
        title="Revoke SCIM token"
        description="Your identity provider can no longer provision users or groups. Existing mappings stay in place but stop receiving updates."
        confirmLabel="Revoke token"
        isPending={revokeTokenIsPending}
        onConfirm={handleRevoke}
      />
    </div>
  )
}
