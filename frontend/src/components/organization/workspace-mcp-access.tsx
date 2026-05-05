"use client"

import { format, formatDistanceToNow } from "date-fns"
import {
  KeyRoundIcon,
  SearchIcon,
  TerminalIcon,
  Trash2Icon,
} from "lucide-react"
import { usePathname, useRouter, useSearchParams } from "next/navigation"
import { useCallback, useEffect, useMemo, useState } from "react"
import type {
  MCPPersonalAccessTokenCreate,
  MCPPersonalAccessTokenIssueResponse,
  MCPPersonalAccessTokenRead,
} from "@/client"
import { CopyButton } from "@/components/copy-button"
import { AlertNotification } from "@/components/notifications"
import {
  AlertDialog,
  AlertDialogAction,
  AlertDialogCancel,
  AlertDialogContent,
  AlertDialogDescription,
  AlertDialogFooter,
  AlertDialogHeader,
  AlertDialogTitle,
} from "@/components/ui/alert-dialog"
import { Badge } from "@/components/ui/badge"
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
  EmptyDescription,
  EmptyHeader,
  EmptyMedia,
  EmptyTitle,
} from "@/components/ui/empty"
import { Input } from "@/components/ui/input"
import { Label } from "@/components/ui/label"
import {
  Select,
  SelectContent,
  SelectItem,
  SelectTrigger,
  SelectValue,
} from "@/components/ui/select"
import { toast } from "@/components/ui/use-toast"
import { useWorkspaceMcpPersonalAccessTokens } from "@/hooks/use-mcp-personal-access-tokens"
import { getApiErrorDetail } from "@/lib/errors"
import { useAppInfo } from "@/lib/hooks"
import { cn } from "@/lib/utils"
import { useWorkspaceId } from "@/providers/workspace-id"

type ExpirationPreset = "never" | "7d" | "30d" | "90d" | "custom"
type TokenStatus = "active" | "expired" | "revoked"
type TokenStatusFilter = "all" | TokenStatus

const CREATE_MCP_TOKEN_PARAM = "createMcpToken"

const STATUS_FILTERS: { value: TokenStatusFilter; label: string }[] = [
  { value: "all", label: "All" },
  { value: "active", label: "Active" },
  { value: "expired", label: "Expired" },
  { value: "revoked", label: "Revoked" },
]

const EXPIRATION_PRESETS: { value: ExpirationPreset; label: string }[] = [
  { value: "never", label: "Never" },
  { value: "7d", label: "7 days" },
  { value: "30d", label: "30 days" },
  { value: "90d", label: "90 days" },
  { value: "custom", label: "Custom" },
]

function addDaysIso(days: number): string {
  const date = new Date()
  date.setDate(date.getDate() + days)
  return date.toISOString()
}

function getPresetExpiresAt(preset: ExpirationPreset): string | null {
  switch (preset) {
    case "never":
      return null
    case "7d":
      return addDaysIso(7)
    case "30d":
      return addDaysIso(30)
    case "90d":
      return addDaysIso(90)
    case "custom":
      return null
  }
}

function getDefaultCustomExpiration(): string {
  const date = new Date()
  date.setDate(date.getDate() + 30)
  const offsetMs = date.getTimezoneOffset() * 60 * 1000
  return new Date(date.getTime() - offsetMs).toISOString().slice(0, 16)
}

function formatRelativeTimestamp(value?: string | null): string {
  if (!value) {
    return "Never"
  }
  return formatDistanceToNow(new Date(value), { addSuffix: true })
}

function formatFullTimestamp(value?: string | null): string {
  if (!value) {
    return "Never"
  }
  return format(new Date(value), "PPpp")
}

function getTokenStatus(token: MCPPersonalAccessTokenRead): TokenStatus {
  if (token.revoked_at) {
    return "revoked"
  }
  if (token.expires_at && new Date(token.expires_at).getTime() <= Date.now()) {
    return "expired"
  }
  return "active"
}

function getStatusConfig(status: TokenStatus): {
  label: string
  dotClassName: string
  textClassName: string
} {
  switch (status) {
    case "active":
      return {
        label: "Active",
        dotClassName: "bg-green-500",
        textClassName: "text-green-700 dark:text-green-400",
      }
    case "expired":
      return {
        label: "Expired",
        dotClassName: "bg-muted-foreground",
        textClassName: "text-muted-foreground",
      }
    case "revoked":
      return {
        label: "Revoked",
        dotClassName: "bg-muted-foreground",
        textClassName: "text-muted-foreground",
      }
  }
}

function tokenMatchesSearch(
  token: MCPPersonalAccessTokenRead,
  query: string
): boolean {
  const normalizedQuery = query.trim().toLowerCase()
  if (!normalizedQuery) {
    return true
  }
  return [token.name, token.preview, token.key_id]
    .filter(Boolean)
    .some((value) => value.toLowerCase().includes(normalizedQuery))
}

function buildMcpUrl(publicAppUrl?: string): string {
  if (publicAppUrl) {
    return `${publicAppUrl.replace(/\/$/, "")}/mcp`
  }
  if (typeof window !== "undefined") {
    return `${window.location.origin}/mcp`
  }
  return "https://<tracecat-app-url>/mcp"
}

function buildClaudeCommand(rawToken: string, mcpUrl: string): string {
  return [
    `claude mcp add --transport http tracecat ${mcpUrl} \\`,
    "  --header 'Authorization: Bearer ${TRACECAT_MCP_PAT}'",
  ].join("\n")
}

function CreateTokenDialog({
  open,
  pending,
  onOpenChange,
  onCreate,
}: {
  open: boolean
  pending: boolean
  onOpenChange: (open: boolean) => void
  onCreate: (requestBody: MCPPersonalAccessTokenCreate) => Promise<void>
}) {
  const [name, setName] = useState("Claude Code")
  const [expirationPreset, setExpirationPreset] =
    useState<ExpirationPreset>("30d")
  const [customExpiration, setCustomExpiration] = useState(
    getDefaultCustomExpiration()
  )

  async function handleSubmit(event: React.FormEvent<HTMLFormElement>) {
    event.preventDefault()
    const trimmedName = name.trim()
    if (!trimmedName) {
      toast({
        title: "Name required",
        description: "MCP tokens must have a name.",
        variant: "destructive",
      })
      return
    }

    let expiresAt = getPresetExpiresAt(expirationPreset)
    if (expirationPreset === "custom") {
      if (!customExpiration) {
        toast({
          title: "Expiration required",
          description: "Choose a custom expiration time or use another preset.",
          variant: "destructive",
        })
        return
      }
      expiresAt = new Date(customExpiration).toISOString()
    }

    await onCreate({
      name: trimmedName,
      expires_at: expiresAt,
    })
  }

  return (
    <Dialog open={open} onOpenChange={onOpenChange}>
      <DialogContent>
        <form onSubmit={handleSubmit} className="space-y-5">
          <DialogHeader>
            <DialogTitle>Create MCP token</DialogTitle>
            <DialogDescription>
              Create a workspace-scoped bearer secret for MCP clients that
              cannot use OIDC.
            </DialogDescription>
          </DialogHeader>

          <div className="space-y-4">
            <div className="space-y-2">
              <Label htmlFor="mcp-token-name">Name</Label>
              <Input
                id="mcp-token-name"
                value={name}
                onChange={(event) => setName(event.target.value)}
                placeholder="Claude Code"
              />
            </div>

            <div className="space-y-2">
              <Label>Expiration</Label>
              <Select
                value={expirationPreset}
                onValueChange={(value) =>
                  setExpirationPreset(value as ExpirationPreset)
                }
              >
                <SelectTrigger>
                  <SelectValue />
                </SelectTrigger>
                <SelectContent>
                  {EXPIRATION_PRESETS.map((preset) => (
                    <SelectItem key={preset.value} value={preset.value}>
                      {preset.label}
                    </SelectItem>
                  ))}
                </SelectContent>
              </Select>
            </div>

            {expirationPreset === "custom" ? (
              <div className="space-y-2">
                <Label htmlFor="mcp-token-custom-expiration">
                  Custom expiration
                </Label>
                <Input
                  id="mcp-token-custom-expiration"
                  type="datetime-local"
                  value={customExpiration}
                  onChange={(event) => setCustomExpiration(event.target.value)}
                />
              </div>
            ) : null}
          </div>

          <DialogFooter>
            <Button
              type="button"
              variant="outline"
              onClick={() => onOpenChange(false)}
            >
              Cancel
            </Button>
            <Button type="submit" disabled={pending}>
              {pending ? "Creating..." : "Create token"}
            </Button>
          </DialogFooter>
        </form>
      </DialogContent>
    </Dialog>
  )
}

function IssuedTokenDialog({
  issuedCredential,
  mcpUrl,
  onClose,
}: {
  issuedCredential: MCPPersonalAccessTokenIssueResponse | null
  mcpUrl: string
  onClose: () => void
}) {
  const rawToken = issuedCredential?.issued_token.raw_token ?? ""
  const envVar = rawToken ? `TRACECAT_MCP_PAT=${rawToken}` : ""
  const command = rawToken ? buildClaudeCommand(rawToken, mcpUrl) : ""

  return (
    <Dialog
      open={issuedCredential !== null}
      onOpenChange={(open) => !open && onClose()}
    >
      <DialogContent className="w-[calc(100vw-2rem)] max-w-2xl overflow-hidden">
        <DialogHeader>
          <DialogTitle>Set up an MCP PAT</DialogTitle>
          <DialogDescription>
            This token is shown once. Save it as a long-lived secret for MCP
            clients that cannot use OAuth.
          </DialogDescription>
        </DialogHeader>

        <div className="min-w-0 space-y-4">
          <div className="min-w-0 space-y-2">
            <Label>1. Save the token</Label>
            <p className="text-xs text-muted-foreground">
              Add this to your shell profile, project env file, direnv, or
              whichever environment launches your MCP client. A one-time export
              only works for that terminal session.
            </p>
            <div className="flex min-w-0 max-w-full items-center gap-2 rounded-md border bg-muted/30 px-3 py-2">
              <code className="min-w-0 flex-1 truncate font-mono text-xs">
                {envVar}
              </code>
              <CopyButton
                value={envVar}
                toastMessage="MCP token environment variable copied."
                tooltipMessage="Copy env var"
              />
            </div>
          </div>

          <div className="min-w-0 space-y-2">
            <Label>2. Add the MCP</Label>
            <p className="text-xs text-muted-foreground">
              Configure your MCP client to send this token as a bearer header.
              If you move or change the environment variable later, re-add the
              server so the client uses the updated value.
            </p>
            <div className="min-w-0 max-w-full rounded-md border bg-muted/30">
              <div className="flex min-w-0 items-center justify-between gap-2 border-b px-3 py-2">
                <div className="min-w-0 text-xs font-medium">Setup command</div>
                <CopyButton
                  value={command}
                  toastMessage="MCP setup command copied."
                  tooltipMessage="Copy command"
                />
              </div>
              <pre className="max-w-full overflow-x-auto p-3 text-xs leading-5">
                <code className="block min-w-0">{command}</code>
              </pre>
            </div>
          </div>
        </div>

        <DialogFooter>
          <Button type="button" onClick={onClose}>
            Done
          </Button>
        </DialogFooter>
      </DialogContent>
    </Dialog>
  )
}

function TokenStatusBadge({ token }: { token: MCPPersonalAccessTokenRead }) {
  const status = getTokenStatus(token)
  const config = getStatusConfig(status)

  return (
    <Badge
      variant="secondary"
      className={cn(
        "h-5 shrink-0 gap-1.5 bg-secondary px-1.5 py-0 text-[10px] font-normal",
        config.textClassName
      )}
    >
      <span
        className={cn("size-1.5 shrink-0 rounded-full", config.dotClassName)}
      />
      <span>{config.label}</span>
    </Badge>
  )
}

function TokenRow({
  token,
  canManage,
  onRevoke,
}: {
  token: MCPPersonalAccessTokenRead
  canManage: boolean
  onRevoke: (token: MCPPersonalAccessTokenRead) => void
}) {
  const status = getTokenStatus(token)

  return (
    <div className="grid grid-cols-[minmax(0,1fr)_auto] gap-4 px-4 py-3">
      <div className="flex min-w-0 gap-3">
        <div className="flex size-8 shrink-0 items-center justify-center rounded-md border bg-muted/30">
          <KeyRoundIcon className="size-4 text-muted-foreground" />
        </div>
        <div className="min-w-0 space-y-2">
          <div className="flex flex-wrap items-center gap-2">
            <div className="truncate text-sm font-medium">{token.name}</div>
            <TokenStatusBadge token={token} />
          </div>
          <div className="flex flex-wrap gap-x-5 gap-y-1 text-xs text-muted-foreground">
            <span>
              Preview{" "}
              <code className="font-mono text-foreground/80">
                {token.preview}
              </code>
            </span>
            <span title={formatFullTimestamp(token.created_at)}>
              Created {formatRelativeTimestamp(token.created_at)}
            </span>
            <span title={formatFullTimestamp(token.expires_at)}>
              Expires {formatRelativeTimestamp(token.expires_at)}
            </span>
            <span title={formatFullTimestamp(token.last_used_at)}>
              Last used {formatRelativeTimestamp(token.last_used_at)}
            </span>
          </div>
        </div>
      </div>

      <div className="flex items-start">
        <Button
          type="button"
          variant="ghost"
          size="sm"
          disabled={!canManage || status === "revoked"}
          onClick={() => onRevoke(token)}
          className="gap-2 text-muted-foreground hover:text-destructive"
        >
          <Trash2Icon className="size-3.5" />
          Revoke
        </Button>
      </div>
    </div>
  )
}

/**
 * Workspace MCP token management screen.
 */
export function WorkspaceMcpAccess() {
  const workspaceId = useWorkspaceId()
  const pathname = usePathname()
  const router = useRouter()
  const searchParams = useSearchParams()
  const { appInfo } = useAppInfo()
  const mcpUrl = buildMcpUrl(appInfo?.public_app_url)
  const [createOpen, setCreateOpen] = useState(false)
  const [issuedCredential, setIssuedCredential] =
    useState<MCPPersonalAccessTokenIssueResponse | null>(null)
  const [revokeTarget, setRevokeTarget] =
    useState<MCPPersonalAccessTokenRead | null>(null)
  const [searchQuery, setSearchQuery] = useState("")
  const [statusFilter, setStatusFilter] = useState<TokenStatusFilter>("all")

  const {
    tokens,
    nextCursor,
    isLoading,
    error,
    createToken,
    createPending,
    revokeToken,
    revokePending,
  } = useWorkspaceMcpPersonalAccessTokens(workspaceId)

  const filteredTokens = useMemo(
    () =>
      tokens.filter((token) => {
        const status = getTokenStatus(token)
        return (
          (statusFilter === "all" || status === statusFilter) &&
          tokenMatchesSearch(token, searchQuery)
        )
      }),
    [searchQuery, statusFilter, tokens]
  )

  const hasActiveFilters =
    searchQuery.trim().length > 0 || statusFilter !== "all"

  const handleCreateSignalConsumed = useCallback(() => {
    if (!pathname || !searchParams?.get(CREATE_MCP_TOKEN_PARAM)) {
      return
    }
    const params = new URLSearchParams(searchParams.toString())
    params.delete(CREATE_MCP_TOKEN_PARAM)
    const next = params.toString()
    router.replace(next ? `${pathname}?${next}` : pathname, { scroll: false })
  }, [pathname, router, searchParams])

  useEffect(() => {
    if (!searchParams?.get(CREATE_MCP_TOKEN_PARAM)) {
      return
    }
    setCreateOpen(true)
    handleCreateSignalConsumed()
  }, [handleCreateSignalConsumed, searchParams])

  async function handleCreate(requestBody: MCPPersonalAccessTokenCreate) {
    try {
      const response = await createToken(requestBody)
      setIssuedCredential(response)
      setCreateOpen(false)
      toast({
        title: "MCP token created",
        description: "Copy the token now. It will not be shown again.",
      })
    } catch (createError) {
      toast({
        title: "Failed to create MCP token",
        description: getApiErrorDetail(createError) ?? "Please try again.",
        variant: "destructive",
      })
    }
  }

  async function handleRevoke() {
    if (!revokeTarget) {
      return
    }
    try {
      await revokeToken(revokeTarget.id)
      toast({
        title: "MCP token revoked",
        description: `${revokeTarget.name} can no longer authenticate.`,
      })
      setRevokeTarget(null)
    } catch (revokeError) {
      toast({
        title: "Failed to revoke MCP token",
        description: getApiErrorDetail(revokeError) ?? "Please try again.",
        variant: "destructive",
      })
    }
  }

  if (error) {
    return (
      <AlertNotification
        level="error"
        message={`Error loading MCP tokens: ${error.message}`}
      />
    )
  }

  return (
    <div className="flex size-full flex-col">
      <div className="shrink-0">
        <header className="flex h-10 items-center gap-3 border-b pl-3 pr-4">
          <div className="flex min-w-0 items-center gap-3">
            <div className="flex size-7 shrink-0 items-center justify-center">
              <SearchIcon className="size-4 text-muted-foreground" />
            </div>
            <Input
              type="text"
              placeholder="Search MCP tokens..."
              value={searchQuery}
              onChange={(event) => setSearchQuery(event.target.value)}
              className={cn(
                "h-7 w-64 border-none bg-transparent p-0 text-sm shadow-none outline-none",
                "placeholder:text-muted-foreground focus-visible:ring-0 focus-visible:ring-offset-0"
              )}
            />
          </div>

          <div className="ml-auto flex items-center gap-3">
            <span className="text-xs text-muted-foreground">
              {filteredTokens.length} tokens
            </span>
          </div>
        </header>

        <div className="flex flex-wrap items-center gap-2 border-b px-4 py-2">
          {STATUS_FILTERS.map((filterOption) => (
            <button
              key={filterOption.value}
              type="button"
              onClick={() => setStatusFilter(filterOption.value)}
              className={cn(
                "flex h-6 items-center rounded-md border border-input bg-transparent px-2 text-xs font-medium transition-colors",
                "hover:bg-muted/50",
                statusFilter === filterOption.value
                  ? "border-primary/50 bg-primary/5 text-foreground"
                  : "text-muted-foreground hover:text-foreground"
              )}
            >
              {filterOption.label}
            </button>
          ))}

          {hasActiveFilters ? (
            <button
              type="button"
              onClick={() => {
                setSearchQuery("")
                setStatusFilter("all")
              }}
              className="flex h-6 items-center gap-1.5 rounded-md px-2 text-xs text-muted-foreground transition-colors hover:bg-muted hover:text-foreground"
            >
              Reset
            </button>
          ) : null}
        </div>
      </div>

      <div className="min-h-0 flex-1 overflow-auto">
        {isLoading ? (
          <div className="px-4 py-3 text-sm text-muted-foreground">
            Loading MCP tokens...
          </div>
        ) : tokens.length === 0 ? (
          <Empty className="rounded-none border-0">
            <EmptyHeader>
              <EmptyMedia variant="icon">
                <TerminalIcon />
              </EmptyMedia>
              <EmptyTitle>No MCP tokens</EmptyTitle>
              <EmptyDescription>
                Create a workspace-scoped token for MCP clients that cannot use
                OIDC.
              </EmptyDescription>
            </EmptyHeader>
          </Empty>
        ) : filteredTokens.length === 0 ? (
          <Empty className="rounded-none border-0">
            <EmptyHeader>
              <EmptyMedia variant="icon">
                <SearchIcon />
              </EmptyMedia>
              <EmptyTitle>No matching MCP tokens</EmptyTitle>
              <EmptyDescription>
                Adjust the search query or filters to find a token.
              </EmptyDescription>
            </EmptyHeader>
          </Empty>
        ) : (
          <div className="divide-y divide-border/50">
            {filteredTokens.map((token) => (
              <TokenRow
                key={token.id}
                token={token}
                canManage
                onRevoke={setRevokeTarget}
              />
            ))}
          </div>
        )}

        {nextCursor ? (
          <p className="px-4 py-3 text-xs text-muted-foreground">
            Only the first page of MCP tokens is shown in this view.
          </p>
        ) : null}
      </div>

      <CreateTokenDialog
        open={createOpen}
        pending={createPending}
        onOpenChange={setCreateOpen}
        onCreate={handleCreate}
      />
      <IssuedTokenDialog
        issuedCredential={issuedCredential}
        mcpUrl={mcpUrl}
        onClose={() => setIssuedCredential(null)}
      />
      <AlertDialog
        open={revokeTarget !== null}
        onOpenChange={(open) => !open && setRevokeTarget(null)}
      >
        <AlertDialogContent>
          <AlertDialogHeader>
            <AlertDialogTitle>Revoke MCP token?</AlertDialogTitle>
            <AlertDialogDescription>
              This immediately prevents {revokeTarget?.name ?? "this token"}{" "}
              from authenticating to the MCP server.
            </AlertDialogDescription>
          </AlertDialogHeader>
          <AlertDialogFooter>
            <AlertDialogCancel disabled={revokePending}>
              Cancel
            </AlertDialogCancel>
            <AlertDialogAction
              variant="destructive"
              disabled={revokePending}
              onClick={(event) => {
                event.preventDefault()
                void handleRevoke()
              }}
            >
              {revokePending ? "Revoking..." : "Revoke token"}
            </AlertDialogAction>
          </AlertDialogFooter>
        </AlertDialogContent>
      </AlertDialog>
    </div>
  )
}
