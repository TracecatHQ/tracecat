"use client"

import {
  ArrowUpRight,
  KeyRoundIcon,
  MoreHorizontal,
  NetworkIcon,
  PlusIcon,
  Trash2Icon,
} from "lucide-react"
import { useState } from "react"
import type {
  ManagedTunnelCreated,
  TunnelGatewayKeyRead,
  TunnelRead,
  TunnelTarget,
} from "@/client"
import { useScopeCheck } from "@/components/auth/scope-guard"
import { CopyButton } from "@/components/copy-button"
import { EntitlementRequiredEmptyState } from "@/components/entitlement-required-empty-state"
import { CenteredSpinner } from "@/components/loading/spinner"
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
import { Button } from "@/components/ui/button"
import {
  Dialog,
  DialogBody,
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
  EmptyDescription,
  EmptyHeader,
  EmptyMedia,
  EmptyTitle,
} from "@/components/ui/empty"
import { Input } from "@/components/ui/input"
import { Label } from "@/components/ui/label"
import { Switch } from "@/components/ui/switch"
import { Textarea } from "@/components/ui/textarea"
import { toast } from "@/components/ui/use-toast"
import { useEntitlements } from "@/hooks/use-entitlements"
import { useTunnelStatus, useTunnels } from "@/hooks/use-tunnels"
import { getApiErrorDetail } from "@/lib/errors"
import { cn } from "@/lib/utils"
import { useWorkspaceId } from "@/providers/workspace-id"

const CREATE_TUNNEL_FORM_ID = "create-tunnel-form"
const TUNNEL_NAME_PATTERN = /^[a-z0-9]([a-z0-9_-]*[a-z0-9])?$/

/**
 * Parses one target per line, written as `host:port[,port...]`.
 * Throws with a user-facing message on the first invalid line.
 */
export function parseTunnelTargets(text: string): TunnelTarget[] {
  const targets: TunnelTarget[] = []
  for (const [index, raw] of text.split("\n").entries()) {
    const line = raw.trim()
    if (!line) continue
    const separator = line.lastIndexOf(":")
    if (separator <= 0) {
      throw new Error(
        `Line ${index + 1}: use host:port, for example splunk.internal:8089`
      )
    }
    const host = line.slice(0, separator).trim()
    const ports = line
      .slice(separator + 1)
      .split(",")
      .map((port) => Number(port.trim()))
    if (
      ports.some((port) => !Number.isInteger(port) || port < 1 || port > 65535)
    ) {
      throw new Error(`Line ${index + 1}: ports must be between 1 and 65535`)
    }
    targets.push({ host, ports })
  }
  if (targets.length === 0) {
    throw new Error("Add at least one target")
  }
  return targets
}

function formatTargets(targets: TunnelTarget[]): string {
  return targets
    .map((target) => `${target.host}:${target.ports.join(",")}`)
    .join(", ")
}

/** Header shared by every state of the tunnels page. */
export function CreateTunnelButton() {
  const workspaceId = useWorkspaceId()
  const canCreate = useScopeCheck("tunnel:create")
  const { hasEntitlement } = useEntitlements()
  const tunnelsEnabled = hasEntitlement("tunnels")
  const { createManagedTunnel, createPending } = useTunnels(workspaceId, {
    enabled: tunnelsEnabled,
  })
  const [createOpen, setCreateOpen] = useState(false)
  const [created, setCreated] = useState<ManagedTunnelCreated | null>(null)

  if (!tunnelsEnabled || canCreate !== true) {
    return null
  }

  return (
    <>
      <Button
        variant="outline"
        size="sm"
        className="h-7 bg-background"
        onClick={() => setCreateOpen(true)}
      >
        <PlusIcon className="mr-1 h-3.5 w-3.5" />
        Create tunnel
      </Button>
      <CreateTunnelDialog
        open={createOpen}
        onOpenChange={setCreateOpen}
        pending={createPending}
        onCreate={async (values) => {
          try {
            const result = await createManagedTunnel(values)
            setCreateOpen(false)
            setCreated(result)
          } catch (err) {
            toast({
              title: "Failed to create tunnel",
              description: getApiErrorDetail(err),
              variant: "destructive",
            })
          }
        }}
      />
      <Dialog
        open={created !== null}
        onOpenChange={(open) => !open && setCreated(null)}
      >
        <DialogContent size="md">
          <DialogHeader>
            <DialogTitle>Install the site connector</DialogTitle>
            <DialogDescription>
              Run this on a host that can reach your targets. It contains a
              secret and is only shown once.
            </DialogDescription>
          </DialogHeader>
          <DialogBody>
            {created ? (
              <SecretValue
                value={created.site_install_command}
                toastMessage="Install command copied"
              />
            ) : null}
          </DialogBody>
        </DialogContent>
      </Dialog>
    </>
  )
}

export function WorkspaceTunnels() {
  const workspaceId = useWorkspaceId()
  const canUpdate = useScopeCheck("tunnel:update")
  const canDelete = useScopeCheck("tunnel:delete")
  const {
    hasEntitlement,
    hasEntitlementData,
    isLoading: entitlementsLoading,
  } = useEntitlements()
  const tunnelsEnabled = hasEntitlement("tunnels")
  const {
    tunnels,
    isLoading,
    error,
    updateTunnel,
    deleteTunnel,
    deletePending,
    issueGatewayKey,
    issueGatewayKeyPending,
  } = useTunnels(workspaceId, { enabled: tunnelsEnabled })

  const [rotateTarget, setRotateTarget] = useState<TunnelRead | null>(null)
  const [issuedKey, setIssuedKey] = useState<TunnelGatewayKeyRead | null>(null)
  const [deleteTarget, setDeleteTarget] = useState<TunnelRead | null>(null)

  if (entitlementsLoading) {
    return <CenteredSpinner />
  }

  let body: React.ReactNode
  if (!hasEntitlementData) {
    body = (
      <div className="flex h-full p-6">
        <EntitlementRequiredEmptyState
          title="Unable to check plan access"
          description="Reload the page to try again."
        >
          <Button
            variant="outline"
            size="sm"
            onClick={() => window.location.reload()}
          >
            Reload
          </Button>
        </EntitlementRequiredEmptyState>
      </div>
    )
  } else if (!tunnelsEnabled) {
    body = (
      <div className="flex h-full p-6">
        <EntitlementRequiredEmptyState
          title="Upgrade required"
          description="Tunnels are unavailable on your current plan."
        >
          <Button
            variant="link"
            asChild
            className="text-muted-foreground"
            size="sm"
          >
            <a
              href="https://tracecat.com"
              target="_blank"
              rel="noopener noreferrer"
            >
              Learn more <ArrowUpRight className="size-4" />
            </a>
          </Button>
        </EntitlementRequiredEmptyState>
      </div>
    )
  } else if (error) {
    body = (
      <div className="p-6">
        <AlertNotification
          level="error"
          message={`Error loading tunnels: ${error.message}`}
        />
      </div>
    )
  } else if (isLoading) {
    body = (
      <div className="flex h-full items-center justify-center">
        <CenteredSpinner />
      </div>
    )
  } else if (tunnels.length === 0) {
    body = (
      <div className="flex h-full p-6">
        <Empty>
          <EmptyHeader>
            <EmptyMedia variant="icon">
              <NetworkIcon className="size-5 text-muted-foreground/60" />
            </EmptyMedia>
            <EmptyTitle>No tunnels yet</EmptyTitle>
            <EmptyDescription>
              Create a tunnel to let actions reach hosts in your private
              network.
            </EmptyDescription>
          </EmptyHeader>
        </Empty>
      </div>
    )
  } else {
    body = (
      <div className="divide-y">
        {tunnels.map((tunnel) => (
          <div
            key={tunnel.id}
            className="group/item flex items-center gap-2 px-4 py-3 transition-colors hover:bg-muted/50"
          >
            <NetworkIcon className="size-4 shrink-0 text-muted-foreground" />
            <div className="flex min-w-0 flex-1 items-center gap-2">
              <div className="flex w-[340px] min-w-0 shrink-0 items-baseline gap-2 text-xs">
                <span className="truncate">{tunnel.name}</span>
                {tunnel.description ? (
                  <span className="min-w-0 truncate text-muted-foreground">
                    {tunnel.description}
                  </span>
                ) : null}
              </div>
              <span
                className="min-w-0 flex-1 truncate font-mono text-xs text-muted-foreground"
                title={formatTargets(tunnel.allowed_targets)}
              >
                {formatTargets(tunnel.allowed_targets)}
              </span>
            </div>
            <div className="shrink-0">
              <TunnelStatusCell workspaceId={workspaceId} tunnel={tunnel} />
            </div>
            <Switch
              aria-label={`Enable ${tunnel.name}`}
              checked={tunnel.enabled}
              disabled={canUpdate !== true}
              onCheckedChange={async (enabled) => {
                try {
                  await updateTunnel({
                    tunnelId: tunnel.id,
                    requestBody: { enabled },
                  })
                } catch (err) {
                  toast({
                    title: "Failed to update tunnel",
                    description: getApiErrorDetail(err),
                    variant: "destructive",
                  })
                }
              }}
            />
            <DropdownMenu>
              <DropdownMenuTrigger asChild>
                <Button
                  variant="ghost"
                  size="icon"
                  className="size-7"
                  aria-label={`Actions for ${tunnel.name}`}
                >
                  <MoreHorizontal className="size-4" />
                </Button>
              </DropdownMenuTrigger>
              <DropdownMenuContent align="end">
                <DropdownMenuItem
                  disabled={canUpdate !== true}
                  onSelect={() => setRotateTarget(tunnel)}
                >
                  <KeyRoundIcon className="mr-2 size-4" />
                  Rotate gateway key
                </DropdownMenuItem>
                <DropdownMenuItem
                  disabled={canDelete !== true}
                  className="text-destructive"
                  onSelect={() => setDeleteTarget(tunnel)}
                >
                  <Trash2Icon className="mr-2 size-4" />
                  Delete
                </DropdownMenuItem>
              </DropdownMenuContent>
            </DropdownMenu>
          </div>
        ))}
      </div>
    )
  }

  return (
    <div className="flex size-full flex-col overflow-hidden">
      <div className="min-h-0 flex-1 overflow-auto">{body}</div>

      <AlertDialog
        open={rotateTarget !== null}
        onOpenChange={(open) => !open && setRotateTarget(null)}
      >
        <AlertDialogContent>
          <AlertDialogHeader>
            <AlertDialogTitle>Rotate gateway key</AlertDialogTitle>
            <AlertDialogDescription>
              The current key stops working immediately. Tracecat-run gateways
              restart with the new key; gateways you run yourself must be
              updated.
            </AlertDialogDescription>
          </AlertDialogHeader>
          <AlertDialogFooter>
            <AlertDialogCancel>Cancel</AlertDialogCancel>
            <AlertDialogAction
              disabled={issueGatewayKeyPending}
              onClick={async () => {
                if (!rotateTarget) return
                try {
                  setIssuedKey(await issueGatewayKey(rotateTarget.id))
                } catch (err) {
                  toast({
                    title: "Failed to rotate gateway key",
                    description: getApiErrorDetail(err),
                    variant: "destructive",
                  })
                } finally {
                  setRotateTarget(null)
                }
              }}
            >
              Rotate
            </AlertDialogAction>
          </AlertDialogFooter>
        </AlertDialogContent>
      </AlertDialog>

      <Dialog
        open={issuedKey !== null}
        onOpenChange={(open) => !open && setIssuedKey(null)}
      >
        <DialogContent size="md">
          <DialogHeader>
            <DialogTitle>New gateway key</DialogTitle>
            <DialogDescription>
              Only needed for gateways you run yourself. This key is only shown
              once.
            </DialogDescription>
          </DialogHeader>
          <DialogBody>
            {issuedKey ? (
              <SecretValue
                value={issuedKey.gateway_key}
                toastMessage="Gateway key copied"
              />
            ) : null}
          </DialogBody>
        </DialogContent>
      </Dialog>

      <AlertDialog
        open={deleteTarget !== null}
        onOpenChange={(open) => !open && setDeleteTarget(null)}
      >
        <AlertDialogContent>
          <AlertDialogHeader>
            <AlertDialogTitle>Delete {deleteTarget?.name}?</AlertDialogTitle>
            <AlertDialogDescription>
              Actions that use this tunnel will fail until they are pointed at
              another tunnel.
            </AlertDialogDescription>
          </AlertDialogHeader>
          <AlertDialogFooter>
            <AlertDialogCancel>Cancel</AlertDialogCancel>
            <AlertDialogAction
              variant="destructive"
              disabled={deletePending}
              onClick={async () => {
                if (!deleteTarget) return
                try {
                  await deleteTunnel(deleteTarget.id)
                } catch (err) {
                  toast({
                    title: "Failed to delete tunnel",
                    description: getApiErrorDetail(err),
                    variant: "destructive",
                  })
                } finally {
                  setDeleteTarget(null)
                }
              }}
            >
              Delete
            </AlertDialogAction>
          </AlertDialogFooter>
        </AlertDialogContent>
      </AlertDialog>
    </div>
  )
}

function SecretValue({
  value,
  toastMessage,
}: {
  value: string
  toastMessage: string
}) {
  return (
    <div className="flex min-w-0 max-w-full items-start gap-3 rounded-lg bg-muted/60 px-4 py-3.5">
      <code className="min-w-0 flex-1 whitespace-pre-wrap break-all font-mono text-xs text-foreground/90 select-all">
        {value}
      </code>
      <CopyButton
        value={value}
        toastMessage={toastMessage}
        tooltipMessage="Copy"
        className="size-6 shrink-0"
        iconClassName="size-4 text-foreground/70"
      />
    </div>
  )
}

function StatusDot({ label, ok }: { label: string; ok: boolean | null }) {
  let color = "bg-muted-foreground/40"
  if (ok === true) color = "bg-emerald-500"
  if (ok === false) color = "bg-red-500"
  return (
    <span className="inline-flex items-center gap-1.5 text-xs text-muted-foreground">
      <span className={cn("size-1.5 rounded-full", color)} />
      {label}
    </span>
  )
}

function TunnelStatusCell({
  workspaceId,
  tunnel,
}: {
  workspaceId: string
  tunnel: TunnelRead
}) {
  const { data, isLoading, error } = useTunnelStatus(workspaceId, tunnel.id, {
    enabled: tunnel.enabled,
  })
  if (!tunnel.enabled) {
    return <span className="text-xs text-muted-foreground">Disabled</span>
  }
  if (isLoading) {
    return <span className="text-xs text-muted-foreground">Checking…</span>
  }
  if (error || !data) {
    return <StatusDot label="Unknown" ok={null} />
  }
  return (
    <div className="flex items-center gap-3">
      {data.site_online !== null ? (
        <StatusDot
          label={data.site_online ? "Site online" : "Site offline"}
          ok={data.site_online}
        />
      ) : null}
      {data.gateway_ready !== null && data.gateway_ready !== undefined ? (
        <StatusDot
          label={data.gateway_ready ? "Gateway ready" : "Gateway starting"}
          ok={data.gateway_ready}
        />
      ) : null}
    </div>
  )
}

function CreateTunnelDialog({
  open,
  onOpenChange,
  pending,
  onCreate,
}: {
  open: boolean
  onOpenChange: (open: boolean) => void
  pending: boolean
  onCreate: (values: {
    name: string
    description: string | null
    allowed_targets: TunnelTarget[]
  }) => Promise<void>
}) {
  const [name, setName] = useState("")
  const [description, setDescription] = useState("")
  const [targets, setTargets] = useState("")
  const [formError, setFormError] = useState<string | null>(null)

  function reset() {
    setName("")
    setDescription("")
    setTargets("")
    setFormError(null)
  }

  return (
    <Dialog
      open={open}
      onOpenChange={(next) => {
        if (!next) reset()
        onOpenChange(next)
      }}
    >
      <DialogContent size="md">
        <DialogHeader>
          <DialogTitle>Create tunnel</DialogTitle>
          <DialogDescription>
            Tracecat provisions the Pangolin site, client, and resources for
            each target.
          </DialogDescription>
        </DialogHeader>
        <DialogBody>
          <form
            id={CREATE_TUNNEL_FORM_ID}
            className="space-y-4"
            onSubmit={async (event) => {
              event.preventDefault()
              const trimmed = name.trim()
              if (!TUNNEL_NAME_PATTERN.test(trimmed) || trimmed.length > 63) {
                setFormError(
                  "Name must be lowercase letters, digits, - or _, up to 63 characters."
                )
                return
              }
              let allowedTargets: TunnelTarget[]
              try {
                allowedTargets = parseTunnelTargets(targets)
              } catch (err) {
                setFormError(err instanceof Error ? err.message : String(err))
                return
              }
              setFormError(null)
              await onCreate({
                name: trimmed,
                description: description.trim() || null,
                allowed_targets: allowedTargets,
              })
              reset()
            }}
          >
            <div className="space-y-2">
              <Label htmlFor="tunnel-name" className="text-xs">
                Name
              </Label>
              <Input
                id="tunnel-name"
                className="text-xs"
                placeholder="datacenter-1"
                value={name}
                onChange={(event) => setName(event.target.value)}
              />
            </div>
            <div className="space-y-2">
              <Label htmlFor="tunnel-description" className="text-xs">
                Description
              </Label>
              <Input
                id="tunnel-description"
                className="text-xs"
                placeholder="Optional"
                value={description}
                onChange={(event) => setDescription(event.target.value)}
              />
            </div>
            <div className="space-y-2">
              <Label htmlFor="tunnel-targets" className="text-xs">
                Targets
              </Label>
              <Textarea
                id="tunnel-targets"
                className="min-h-[120px] font-mono text-xs"
                placeholder={"splunk.internal:8089\n10.0.0.5:443,5986"}
                value={targets}
                onChange={(event) => setTargets(event.target.value)}
              />
              <p className="text-xs text-muted-foreground">
                One host per line as host:port. Separate multiple ports with
                commas. Actions can only reach these hosts and ports.
              </p>
            </div>
            {formError ? (
              <p className="text-xs text-destructive">{formError}</p>
            ) : null}
          </form>
        </DialogBody>
        <DialogFooter>
          <Button
            size="sm"
            type="submit"
            form={CREATE_TUNNEL_FORM_ID}
            disabled={pending}
          >
            Create
          </Button>
        </DialogFooter>
      </DialogContent>
    </Dialog>
  )
}
