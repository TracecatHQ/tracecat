"use client"

import { DotsHorizontalIcon } from "@radix-ui/react-icons"
import { KeyRoundIcon, Loader2 } from "lucide-react"
import { useEffect, useState } from "react"
import type { ScimConnectionRead } from "@/client"
import { useScopeCheck } from "@/components/auth/scope-guard"
import { CopyButton } from "@/components/copy-button"
import { CenteredSpinner } from "@/components/loading/spinner"
import { Alert, AlertDescription, AlertTitle } from "@/components/ui/alert"
import {
  AlertDialog,
  AlertDialogCancel,
  AlertDialogContent,
  AlertDialogDescription,
  AlertDialogFooter,
  AlertDialogHeader,
  AlertDialogTitle,
} from "@/components/ui/alert-dialog"
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
  DropdownMenu,
  DropdownMenuContent,
  DropdownMenuItem,
  DropdownMenuTrigger,
} from "@/components/ui/dropdown-menu"
import {
  Empty,
  EmptyContent,
  EmptyDescription,
  EmptyHeader,
  EmptyMedia,
  EmptyTitle,
} from "@/components/ui/empty"
import { useScimConnection, useScimDirectorySummary } from "@/hooks/use-scim"
import { getBaseUrl } from "@/lib/api"
import { formatRelative } from "@/lib/time"

const SCIM_DOCS_URL = "https://docs.tracecat.com/authentication/scim"

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

function tokenUsage(connection: ScimConnectionRead): string {
  if (connection.revoked_at) {
    return `Revoked ${formatRelative(connection.revoked_at) ?? ""}`.trim()
  }
  const lastUsed = formatRelative(connection.last_used_at)
  return lastUsed ? `Last used ${lastUsed}` : "Never used"
}

function ConnectionDetails({
  connection,
  baseUrl,
}: {
  connection: ScimConnectionRead
  baseUrl: string | null
}) {
  const { directorySummary } = useScimDirectorySummary({ enabled: true })
  const users = directorySummary?.users
  const groups = directorySummary?.groups
  return (
    <dl className="grid grid-cols-[140px_1fr] items-center gap-x-6 gap-y-3 px-5 py-4 text-sm">
      <dt className="text-muted-foreground">SCIM base URL</dt>
      <dd className="flex min-w-0 items-center gap-2">
        <code className="min-w-0 truncate rounded bg-muted px-2 py-1 font-mono text-xs select-all">
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

      <dt className="text-muted-foreground">Bearer token</dt>
      <dd className="flex min-w-0 items-center gap-3">
        <code className="font-mono text-xs">{connection.preview}</code>
        <span className="text-xs text-muted-foreground">
          {tokenUsage(connection)}
        </span>
      </dd>

      <dt className="text-muted-foreground">Directory</dt>
      <dd>
        {users && groups ? (
          <>
            {users.total} {users.total === 1 ? "user" : "users"}{" "}
            <span className="text-muted-foreground">
              ({users.inactive} inactive)
            </span>{" "}
            · {groups.total} {groups.total === 1 ? "group" : "groups"}
          </>
        ) : (
          <span className="text-muted-foreground">—</span>
        )}
      </dd>
    </dl>
  )
}

/**
 * Issue and rotate the SCIM connection token, or disconnect SCIM.
 *
 * The raw token is held only in local component state for the lifetime of the
 * dialog that displays it. It is never written to the query cache or refetched,
 * because the API returns it exactly once and can never return it again.
 */
export function OrgSettingsScimConnection({
  onDisconnect,
}: {
  onDisconnect?: () => void
} = {}) {
  const canManage = useScopeCheck("org:scim:manage")
  const baseUrl = useScimBaseUrl()
  const {
    connection,
    connectionIsLoading,
    connectionIsFetching,
    connectionError,
    refetchConnection,
    issueToken,
    issueTokenIsPending,
    disconnect,
    disconnectIsPending,
  } = useScimConnection()

  const [issuedToken, setIssuedToken] = useState<string | null>(null)
  const [rotateOpen, setRotateOpen] = useState(false)
  const [disconnectOpen, setDisconnectOpen] = useState(false)

  async function handleIssue() {
    const issued = await issueToken()
    setIssuedToken(issued.token)
  }

  async function handleRotate() {
    try {
      await handleIssue()
    } finally {
      setRotateOpen(false)
    }
  }

  async function handleDisconnect() {
    await disconnect()
    onDisconnect?.()
    setDisconnectOpen(false)
  }

  if (connectionIsLoading) {
    return <CenteredSpinner />
  }

  const isDisconnected = connection?.status === "disabled"

  return (
    <div className="space-y-4">
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
        <div className="rounded-lg border">
          <div className="flex items-center gap-3 border-b px-5 py-3">
            <h3 className="flex-1 text-sm font-semibold">Connection</h3>
            <a
              href={SCIM_DOCS_URL}
              target="_blank"
              rel="noreferrer"
              className="text-sm underline decoration-muted-foreground/40 underline-offset-4 hover:decoration-foreground"
            >
              Setup guide
            </a>
            <DropdownMenu>
              <DropdownMenuTrigger asChild>
                <Button
                  variant="outline"
                  size="icon"
                  className="size-8"
                  aria-label="Connection actions"
                  disabled={canManage !== true}
                >
                  {issueTokenIsPending || disconnectIsPending ? (
                    <Loader2 className="size-4 animate-spin" />
                  ) : (
                    <DotsHorizontalIcon className="size-4" />
                  )}
                </Button>
              </DropdownMenuTrigger>
              <DropdownMenuContent align="end">
                <DropdownMenuItem
                  disabled={issueTokenIsPending}
                  onSelect={() => setRotateOpen(true)}
                >
                  {isDisconnected ? "Generate new token" : "Rotate token"}
                </DropdownMenuItem>
                {isDisconnected ? null : (
                  <DropdownMenuItem
                    className="text-rose-500 focus:text-rose-600"
                    disabled={disconnectIsPending}
                    onSelect={() => setDisconnectOpen(true)}
                  >
                    Disconnect
                  </DropdownMenuItem>
                )}
              </DropdownMenuContent>
            </DropdownMenu>
          </div>
          <ConnectionDetails connection={connection} baseUrl={baseUrl} />
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
              your identity provider.
            </EmptyDescription>
          </EmptyHeader>
          <EmptyContent>
            <Button
              onClick={() => void handleIssue().catch(() => {})}
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
        <DialogContent className="max-w-lg">
          <DialogHeader>
            <DialogTitle>Copy SCIM credentials</DialogTitle>
            <DialogDescription>
              Paste both into your identity provider. The token is only shown
              once.
            </DialogDescription>
          </DialogHeader>
          {issuedToken ? (
            <div className="space-y-3">
              <CredentialRow label="SCIM base URL" value={baseUrl ?? ""} />
              <CredentialRow label="Bearer token" value={issuedToken} />
            </div>
          ) : null}
          <DialogFooter>
            <Button variant="outline" onClick={() => setIssuedToken(null)}>
              Close
            </Button>
          </DialogFooter>
        </DialogContent>
      </Dialog>

      <AlertDialog open={disconnectOpen} onOpenChange={setDisconnectOpen}>
        <AlertDialogContent>
          <AlertDialogHeader>
            <AlertDialogTitle>
              Disconnect your identity provider?
            </AlertDialogTitle>
            <AlertDialogDescription>
              The token is revoked, and group mappings and synced IdP users and
              groups are removed. Members of mapped groups stay as manual
              members, so nobody loses access. To reconnect, generate a new
              token and push users and groups from your IdP again.
            </AlertDialogDescription>
          </AlertDialogHeader>
          <AlertDialogFooter>
            <AlertDialogCancel disabled={disconnectIsPending}>
              Cancel
            </AlertDialogCancel>
            <Button
              variant="destructive"
              disabled={disconnectIsPending}
              onClick={() => void handleDisconnect().catch(() => {})}
            >
              Disconnect
            </Button>
          </AlertDialogFooter>
        </AlertDialogContent>
      </AlertDialog>

      <AlertDialog open={rotateOpen} onOpenChange={setRotateOpen}>
        <AlertDialogContent>
          <AlertDialogHeader>
            <AlertDialogTitle>Rotate SCIM token</AlertDialogTitle>
            <AlertDialogDescription>
              The current token stops working immediately. Provisioning fails
              until you paste the new token into your identity provider.
            </AlertDialogDescription>
          </AlertDialogHeader>
          <AlertDialogFooter>
            <AlertDialogCancel disabled={issueTokenIsPending}>
              Cancel
            </AlertDialogCancel>
            <Button
              variant="destructive"
              disabled={issueTokenIsPending}
              onClick={() => void handleRotate().catch(() => {})}
            >
              Rotate token
            </Button>
          </AlertDialogFooter>
        </AlertDialogContent>
      </AlertDialog>
    </div>
  )
}

function CredentialRow({ label, value }: { label: string; value: string }) {
  return (
    <div className="space-y-1.5">
      <span className="text-xs text-muted-foreground">{label}</span>
      <div className="flex min-w-0 items-start gap-3 rounded-lg bg-muted/60 px-4 py-3">
        <code className="min-w-0 flex-1 break-all font-mono text-sm text-foreground/90 select-all">
          {value}
        </code>
        <CopyButton
          value={value}
          toastMessage={`${label} copied`}
          tooltipMessage={`Copy ${label.toLowerCase()}`}
          className="size-6 shrink-0"
          iconClassName="size-4 text-foreground/70"
        />
      </div>
    </div>
  )
}
