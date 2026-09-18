"use client"

import { ChevronRightIcon, PlusIcon, Trash2Icon } from "lucide-react"
import * as React from "react"
import type { SecretStoreProvider, SecretStoreRead } from "@/client"
import { CenteredSpinner } from "@/components/loading/spinner"
import { AlertNotification } from "@/components/notifications"
import { Badge } from "@/components/ui/badge"
import { Button } from "@/components/ui/button"
import {
  Collapsible,
  CollapsibleContent,
  CollapsibleTrigger,
} from "@/components/ui/collapsible"
import {
  Dialog,
  DialogContent,
  DialogDescription,
  DialogFooter,
  DialogHeader,
  DialogTitle,
  DialogTrigger,
} from "@/components/ui/dialog"
import { Input } from "@/components/ui/input"
import { Label } from "@/components/ui/label"
import {
  Select,
  SelectContent,
  SelectItem,
  SelectTrigger,
  SelectValue,
} from "@/components/ui/select"
import { Switch } from "@/components/ui/switch"
import { useOrgSecretStores } from "@/hooks/use-secret-stores"
import { useWorkspaceManager } from "@/lib/hooks"
import { cn } from "@/lib/utils"
import {
  type CreateConfigState,
  SECRET_STORE_PROVIDERS,
} from "./secret-store-providers"

/** Organization settings for external secret stores. */
export function OrgSettingsSecretStores() {
  const { stores, isLoading, error } = useOrgSecretStores()
  const [expandedStoreIds, setExpandedStoreIds] = React.useState<Set<string>>(
    () => new Set()
  )

  function setStoreOpen(storeId: string, open: boolean) {
    setExpandedStoreIds((current) => {
      const next = new Set(current)
      if (open) next.add(storeId)
      else next.delete(storeId)
      return next
    })
  }

  if (isLoading) {
    return <CenteredSpinner />
  }
  if (error) {
    return (
      <AlertNotification
        level="error"
        message={`Error loading secret stores: ${error.message}`}
      />
    )
  }

  return (
    <div className="space-y-6">
      <div className="flex flex-col items-start gap-4 sm:flex-row sm:items-center sm:justify-between">
        <p className="max-w-xl text-sm text-muted-foreground">
          Tracecat reads secret values from your secret manager when needed.
          Values are never stored in Tracecat.
        </p>
        <CreateSecretStoreDialog
          onCreated={(storeId) => setStoreOpen(storeId, true)}
        />
      </div>
      {!stores || stores.length === 0 ? (
        <div className="space-y-1 rounded-lg border p-6 text-sm">
          <p>No external secret stores configured yet.</p>
          <p className="text-muted-foreground">
            Add a store to make it available to selected workspaces.
          </p>
        </div>
      ) : (
        <div className="space-y-4">
          {stores.map((store) => (
            <SecretStoreCard
              key={store.id}
              store={store}
              open={expandedStoreIds.has(store.id)}
              onOpenChange={(open) => setStoreOpen(store.id, open)}
            />
          ))}
        </div>
      )}
    </div>
  )
}

function CreateSecretStoreDialog({
  onCreated,
}: {
  onCreated: (storeId: string) => void
}) {
  const { createStore, createStorePending } = useOrgSecretStores()
  const [open, setOpen] = React.useState(false)
  const [name, setName] = React.useState("")
  const [config, setConfig] = React.useState<CreateConfigState>({})
  const [enabled, setEnabled] = React.useState(true)
  // A provider select arrives with the second provider.
  const providerKey = Object.keys(
    SECRET_STORE_PROVIDERS
  )[0] as SecretStoreProvider
  const provider = SECRET_STORE_PROVIDERS[providerKey]

  async function handleSubmit(event: React.FormEvent<HTMLFormElement>) {
    event.preventDefault()
    try {
      const store = await createStore({
        name: name.trim(),
        config: provider.toCreateConfig(config),
        enabled,
      })
      onCreated(store.id)
    } catch {
      // The mutation hook shows the error; keep the draft available to retry.
      return
    }
    setOpen(false)
    setName("")
    setConfig({})
    setEnabled(true)
  }

  return (
    <Dialog open={open} onOpenChange={setOpen}>
      <DialogTrigger asChild>
        <Button size="sm" variant="outline" className="shrink-0 shadow-none">
          <PlusIcon className="mr-2 size-4" />
          Add store
        </Button>
      </DialogTrigger>
      <DialogContent>
        <DialogHeader>
          <DialogTitle>{provider.createTitle}</DialogTitle>
          <DialogDescription>{provider.createDescription}</DialogDescription>
        </DialogHeader>
        <form onSubmit={handleSubmit} className="space-y-5">
          <div className="space-y-2">
            <Label htmlFor="store-name">Name</Label>
            <Input
              id="store-name"
              value={name}
              onChange={(e) => setName(e.target.value)}
              placeholder="production-secrets"
              required
            />
          </div>
          <provider.CreateFields config={config} onChange={setConfig} />
          <div className="flex items-center justify-between gap-4 border-t pt-4">
            <div className="space-y-0.5">
              <Label htmlFor="store-enabled">Enabled</Label>
              <p className="text-xs text-muted-foreground">
                Disabled stores cannot be used to read secrets.
              </p>
            </div>
            <Switch
              id="store-enabled"
              checked={enabled}
              onCheckedChange={setEnabled}
            />
          </div>
          <DialogFooter className="gap-2 sm:gap-0">
            <Button
              type="button"
              variant="outline"
              className="shadow-none"
              onClick={() => setOpen(false)}
              disabled={createStorePending}
            >
              Cancel
            </Button>
            <Button
              type="submit"
              disabled={createStorePending}
              className="shadow-none"
            >
              {createStorePending ? "Saving…" : "Save store"}
            </Button>
          </DialogFooter>
        </form>
      </DialogContent>
    </Dialog>
  )
}

function SecretStoreCard({
  store,
  open,
  onOpenChange,
}: {
  store: SecretStoreRead
  open: boolean
  onOpenChange: (open: boolean) => void
}) {
  const { updateStore, deleteStore, authorizeWorkspace, revokeWorkspace } =
    useOrgSecretStores()
  const { workspaces } = useWorkspaceManager()
  const [selectedWorkspaceId, setSelectedWorkspaceId] = React.useState("")

  const authorizedIds = new Set(store.authorized_workspace_ids ?? [])
  const authorizedWorkspaces = (workspaces ?? []).filter((ws) =>
    authorizedIds.has(ws.id)
  )
  const unauthorizedWorkspaces = (workspaces ?? []).filter(
    (ws) => !authorizedIds.has(ws.id)
  )
  const referenceCount = store.reference_count ?? 0
  const provider = SECRET_STORE_PROVIDERS[store.provider]

  return (
    <Collapsible
      open={open}
      onOpenChange={onOpenChange}
      className="rounded-lg border p-5"
    >
      <div className="flex flex-col gap-3 sm:flex-row sm:items-start sm:justify-between">
        <CollapsibleTrigger className="flex min-w-0 flex-1 items-start gap-3 rounded-sm text-left outline-none focus-visible:ring-1 focus-visible:ring-inset focus-visible:ring-ring">
          <ChevronRightIcon
            className={cn(
              "mt-0.5 size-4 shrink-0 text-muted-foreground transition-transform motion-reduce:transition-none",
              open && "rotate-90"
            )}
          />
          <span className="min-w-0 space-y-2">
            <span className="flex flex-wrap items-center gap-2">
              <span className="break-all text-sm font-medium">
                {store.name}
              </span>
              <span className="rounded-md border px-2 py-0.5 text-xs font-medium">
                {provider.label}
              </span>
            </span>
            <span className="flex flex-wrap gap-x-4 gap-y-1 text-xs text-muted-foreground">
              <span>{provider.summary(store)}</span>
              <span>
                {referenceCount} secret reference
                {referenceCount === 1 ? "" : "s"}
              </span>
            </span>
          </span>
        </CollapsibleTrigger>
        <div className="flex shrink-0 items-center gap-3 pl-7 sm:pl-0">
          <div className="flex items-center gap-2">
            <Label
              htmlFor={`store-enabled-${store.id}`}
              className="text-xs text-muted-foreground"
            >
              {store.enabled ? "Enabled" : "Disabled"}
            </Label>
            <Switch
              id={`store-enabled-${store.id}`}
              checked={store.enabled}
              onCheckedChange={(checked) =>
                updateStore({ storeId: store.id, params: { enabled: checked } })
              }
            />
          </div>
          <Button
            size="sm"
            variant="ghost"
            aria-label={`Delete ${store.name}`}
            disabled={referenceCount > 0}
            title={
              referenceCount > 0
                ? "Remove all secret references before deleting this store"
                : "Delete store"
            }
            onClick={() => deleteStore(store.id)}
          >
            <Trash2Icon className="size-4" />
          </Button>
        </div>
      </div>

      <CollapsibleContent className="motion-reduce:animate-none">
        <div className="space-y-5 pt-6">
          <provider.Details store={store} />
          <div className="space-y-3 border-t pt-5">
            <div className="space-y-1">
              <p className="text-xs font-medium">Authorized workspaces</p>
              <p className="text-xs text-muted-foreground">
                Credential authors in these workspaces can reference secrets
                allowed by the store.
              </p>
            </div>
            <div className="flex flex-wrap gap-2">
              {authorizedWorkspaces.length === 0 && (
                <p className="text-xs text-muted-foreground">
                  No workspaces can reference this store yet.
                </p>
              )}
              {authorizedWorkspaces.map((ws) => (
                <Badge key={ws.id} variant="secondary" className="gap-1">
                  {ws.name}
                  <button
                    type="button"
                    aria-label={`Revoke ${ws.name}`}
                    className="ml-1 text-muted-foreground hover:text-foreground"
                    onClick={() =>
                      revokeWorkspace({ storeId: store.id, workspaceId: ws.id })
                    }
                  >
                    ×
                  </button>
                </Badge>
              ))}
            </div>
            <div className="flex items-center gap-2">
              <Select
                value={selectedWorkspaceId}
                onValueChange={setSelectedWorkspaceId}
              >
                <SelectTrigger
                  className="w-64 min-w-0 text-sm"
                  aria-label="Select a workspace"
                >
                  <SelectValue placeholder="Select a workspace" />
                </SelectTrigger>
                <SelectContent>
                  {unauthorizedWorkspaces.map((ws) => (
                    <SelectItem key={ws.id} value={ws.id}>
                      {ws.name}
                    </SelectItem>
                  ))}
                </SelectContent>
              </Select>
              <Button
                size="sm"
                variant="outline"
                className="shrink-0 shadow-none"
                disabled={!selectedWorkspaceId}
                onClick={async () => {
                  await authorizeWorkspace({
                    storeId: store.id,
                    workspaceId: selectedWorkspaceId,
                  })
                  setSelectedWorkspaceId("")
                }}
              >
                Authorize
              </Button>
            </div>
          </div>
        </div>
      </CollapsibleContent>
    </Collapsible>
  )
}
