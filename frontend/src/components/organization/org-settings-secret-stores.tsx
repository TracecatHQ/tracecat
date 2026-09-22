"use client"

import {
  ChevronDownIcon,
  ChevronRightIcon,
  EllipsisIcon,
  PencilIcon,
  PlusIcon,
  Trash2Icon,
} from "lucide-react"
import * as React from "react"
import type { SecretStoreProvider, SecretStoreRead } from "@/client"
import { ScopeGuard, useScopeCheck } from "@/components/auth/scope-guard"
import { CenteredSpinner } from "@/components/loading/spinner"
import { AlertNotification } from "@/components/notifications"
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
import {
  DropdownMenu,
  DropdownMenuCheckboxItem,
  DropdownMenuContent,
  DropdownMenuItem,
  DropdownMenuRadioGroup,
  DropdownMenuRadioItem,
  DropdownMenuSeparator,
  DropdownMenuTrigger,
} from "@/components/ui/dropdown-menu"
import { Input } from "@/components/ui/input"
import { Label } from "@/components/ui/label"
import { Switch } from "@/components/ui/switch"
import { useOrgSecretStores } from "@/hooks/use-secret-stores"
import { useWorkspaceManager } from "@/lib/hooks"
import {
  type CreateConfigState,
  SECRET_STORE_PROVIDERS,
} from "./secret-store-providers"

/** Organization settings for external secret stores. */
export function OrgSettingsSecretStores() {
  const { stores, isLoading, error } = useOrgSecretStores()
  const [detailsStoreId, setDetailsStoreId] = React.useState<string | null>(
    null
  )

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
        <ScopeGuard scope="org:secret:create">
          <CreateSecretStoreDialog onCreated={setDetailsStoreId} />
        </ScopeGuard>
      </div>
      {!stores || stores.length === 0 ? (
        <div className="space-y-1 rounded-lg border p-6 text-sm">
          <p>No external secret stores configured yet.</p>
          <p className="text-muted-foreground">
            Add a store to make it available to your workspaces.
          </p>
        </div>
      ) : (
        <div className="space-y-4">
          {stores.map((store) => (
            <SecretStoreCard
              key={store.id}
              store={store}
              detailsOpen={detailsStoreId === store.id}
              onDetailsOpenChange={(open) =>
                setDetailsStoreId(open ? store.id : null)
              }
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
  const [allWorkspaces, setAllWorkspaces] = React.useState(false)
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
        all_workspaces: allWorkspaces,
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
    setAllWorkspaces(false)
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
          <div className="flex items-center justify-between gap-4">
            <div className="space-y-0.5">
              <Label htmlFor="store-all-workspaces">All workspaces</Label>
            </div>
            <Switch
              id="store-all-workspaces"
              checked={allWorkspaces}
              onCheckedChange={setAllWorkspaces}
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

function EditSecretStoreDialog({
  store,
  onClose,
}: {
  store: SecretStoreRead
  onClose: () => void
}) {
  const { updateStore } = useOrgSecretStores()
  const [name, setName] = React.useState(store.name)
  const [config, setConfig] = React.useState<CreateConfigState>({
    ...store.config,
  })
  const [pending, setPending] = React.useState(false)
  const provider = SECRET_STORE_PROVIDERS[store.provider]

  async function handleSubmit(event: React.FormEvent<HTMLFormElement>) {
    event.preventDefault()
    setPending(true)
    try {
      await updateStore({
        storeId: store.id,
        params: { name: name.trim(), config: provider.toCreateConfig(config) },
      })
      onClose()
    } catch {
      // The mutation hook shows the error; keep the draft available to retry.
    } finally {
      setPending(false)
    }
  }

  return (
    <Dialog
      open
      onOpenChange={(open) => {
        if (!open) onClose()
      }}
    >
      <DialogContent>
        <DialogTitle>Edit secret store</DialogTitle>
        <DialogDescription>
          Update the store connection. The external ID stays the same.
        </DialogDescription>
        <form onSubmit={handleSubmit} className="space-y-5">
          <div className="space-y-2">
            <Label htmlFor="edit-store-name">Name</Label>
            <Input
              id="edit-store-name"
              value={name}
              onChange={(event) => setName(event.target.value)}
              required
            />
          </div>
          <provider.CreateFields config={config} onChange={setConfig} />
          <DialogFooter className="gap-2 sm:gap-0">
            <Button
              type="button"
              variant="outline"
              className="shadow-none"
              onClick={onClose}
              disabled={pending}
            >
              Cancel
            </Button>
            <Button type="submit" className="shadow-none" disabled={pending}>
              {pending ? "Saving…" : "Save changes"}
            </Button>
          </DialogFooter>
        </form>
      </DialogContent>
    </Dialog>
  )
}

function SecretStoreCard({
  store,
  detailsOpen,
  onDetailsOpenChange,
}: {
  store: SecretStoreRead
  detailsOpen: boolean
  onDetailsOpenChange: (open: boolean) => void
}) {
  const { updateStore, deleteStore, authorizeWorkspace, revokeWorkspace } =
    useOrgSecretStores()
  const { workspaces, workspacesLoading, workspacesError } =
    useWorkspaceManager()
  const canUpdate = useScopeCheck("org:secret:update")
  const canDelete = useScopeCheck("org:secret:delete")
  const [editing, setEditing] = React.useState(false)
  const [pending, setPending] = React.useState(false)
  const [workspacePickerOpen, setWorkspacePickerOpen] = React.useState(false)
  const authorizedIds = new Set(store.authorized_workspace_ids ?? [])

  async function changeStore(action: () => Promise<unknown>) {
    setPending(true)
    try {
      await action()
    } catch {
      // Mutation hooks show errors; retain the server's current access state.
    } finally {
      setPending(false)
    }
  }

  const referenceCount = store.reference_count ?? 0
  const provider = SECRET_STORE_PROVIDERS[store.provider]
  let workspaceSummary = "Select workspaces"
  if (store.all_workspaces) {
    workspaceSummary = "All workspaces"
  } else if (authorizedIds.size === 1) {
    workspaceSummary =
      workspaces?.find((workspace) => authorizedIds.has(workspace.id))?.name ??
      "1 workspace"
  } else if (authorizedIds.size > 1) {
    workspaceSummary = `${authorizedIds.size} workspaces`
  }

  return (
    <Collapsible
      open={detailsOpen}
      onOpenChange={onDetailsOpenChange}
      className="rounded-lg border p-4"
    >
      <div className="flex flex-col gap-3 sm:flex-row sm:items-start sm:justify-between">
        <div className="min-w-0 flex-1 space-y-1">
          <h3 className="break-all text-sm font-medium">{store.name}</h3>
          <p className="text-xs text-muted-foreground">
            {provider.label} · {provider.summary(store)}
          </p>
        </div>
        <div className="flex shrink-0 items-center gap-3">
          <ScopeGuard scope="org:secret:update">
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
                disabled={pending}
                onCheckedChange={(checked) =>
                  changeStore(() =>
                    updateStore({
                      storeId: store.id,
                      params: { enabled: checked },
                    })
                  )
                }
              />
            </div>
          </ScopeGuard>
          {(canUpdate || canDelete) && (
            <DropdownMenu>
              <DropdownMenuTrigger asChild>
                <Button
                  size="icon"
                  variant="ghost"
                  className="size-6 text-muted-foreground"
                  aria-label={`Actions for ${store.name}`}
                >
                  <EllipsisIcon className="size-4" />
                </Button>
              </DropdownMenuTrigger>
              <DropdownMenuContent align="end" className="w-56 shadow-none">
                {canUpdate && (
                  <DropdownMenuItem
                    disabled={pending}
                    onSelect={() => setEditing(true)}
                    className="gap-2"
                  >
                    <PencilIcon className="size-3.5" />
                    Edit store
                  </DropdownMenuItem>
                )}
                <ScopeGuard scope="org:secret:delete">
                  <DropdownMenuItem
                    disabled={pending || referenceCount > 0}
                    onSelect={() => changeStore(() => deleteStore(store.id))}
                    className="gap-2 text-destructive focus:text-destructive"
                  >
                    <Trash2Icon className="size-3.5" />
                    Delete store
                  </DropdownMenuItem>
                  {referenceCount > 0 && (
                    <p className="px-2 py-1 text-xs text-muted-foreground">
                      Remove the {referenceCount} secret reference
                      {referenceCount === 1 ? "" : "s"} first.
                    </p>
                  )}
                </ScopeGuard>
              </DropdownMenuContent>
            </DropdownMenu>
          )}
        </div>
      </div>

      <div className="mt-5 flex flex-wrap items-center justify-between gap-3">
        <CollapsibleTrigger asChild>
          <Button
            variant="link"
            className="h-auto gap-1.5 p-0 text-xs font-normal text-muted-foreground hover:text-foreground [&[data-state=open]>svg]:rotate-90"
          >
            <ChevronRightIcon className="size-3.5 shrink-0 transition-transform motion-reduce:transition-none" />
            Connection details
          </Button>
        </CollapsibleTrigger>
        <div className="flex min-w-0 max-w-full items-center gap-3">
          <p className="text-xs text-muted-foreground">Workspaces</p>
          <DropdownMenu
            open={workspacePickerOpen}
            onOpenChange={setWorkspacePickerOpen}
          >
            <DropdownMenuTrigger asChild>
              <Button
                size="sm"
                variant="outline"
                className="h-7 max-w-full gap-2 px-2 text-xs font-normal shadow-none"
                aria-label={`Workspaces: ${workspaceSummary}`}
              >
                <span className="truncate">{workspaceSummary}</span>
                <ChevronDownIcon className="size-3.5 shrink-0 text-muted-foreground" />
              </Button>
            </DropdownMenuTrigger>
            <DropdownMenuContent
              align="end"
              className="w-64 max-w-[calc(100vw-2rem)] shadow-none"
            >
              <DropdownMenuRadioGroup
                value={store.all_workspaces ? "all" : "selected"}
                onValueChange={(value) =>
                  changeStore(() =>
                    updateStore({
                      storeId: store.id,
                      params: { all_workspaces: value === "all" },
                    })
                  )
                }
              >
                <DropdownMenuRadioItem
                  value="all"
                  disabled={!canUpdate || pending}
                  onSelect={(event) => event.preventDefault()}
                  className="py-2"
                >
                  All workspaces
                </DropdownMenuRadioItem>
                <DropdownMenuRadioItem
                  value="selected"
                  disabled={!canUpdate || pending}
                  onSelect={(event) => event.preventDefault()}
                  className="py-2"
                >
                  Selected workspaces
                </DropdownMenuRadioItem>
              </DropdownMenuRadioGroup>
              {!store.all_workspaces && (
                <>
                  <DropdownMenuSeparator />
                  {workspacesLoading && (
                    <p className="px-2 py-2 text-xs text-muted-foreground">
                      Loading workspaces…
                    </p>
                  )}
                  {workspacesError && (
                    <p className="px-2 py-2 text-xs text-destructive">
                      Could not load workspaces. Refresh to try again.
                    </p>
                  )}
                  <div className="max-h-64 overflow-y-auto">
                    {workspaces?.map((workspace) => (
                      <DropdownMenuCheckboxItem
                        key={workspace.id}
                        checked={authorizedIds.has(workspace.id)}
                        disabled={!canUpdate || pending}
                        onSelect={(event) => event.preventDefault()}
                        onCheckedChange={(checked) =>
                          changeStore(() => {
                            const mutate = checked
                              ? authorizeWorkspace
                              : revokeWorkspace
                            return mutate({
                              storeId: store.id,
                              workspaceId: workspace.id,
                            })
                          })
                        }
                        className="break-words py-2"
                      >
                        {workspace.name}
                      </DropdownMenuCheckboxItem>
                    ))}
                  </div>
                </>
              )}
              <DropdownMenuSeparator />
              <DropdownMenuItem className="justify-center py-2">
                Done
              </DropdownMenuItem>
            </DropdownMenuContent>
          </DropdownMenu>
        </div>
      </div>
      <CollapsibleContent className="mt-4 space-y-4 border-t pt-4 motion-reduce:animate-none">
        <p className="text-xs text-muted-foreground">
          {referenceCount} secret reference{referenceCount === 1 ? "" : "s"}
        </p>
        <provider.Details store={store} />
      </CollapsibleContent>
      {editing && (
        <EditSecretStoreDialog
          store={store}
          onClose={() => setEditing(false)}
        />
      )}
    </Collapsible>
  )
}
