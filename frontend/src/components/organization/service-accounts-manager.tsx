"use client"

import { formatDistanceToNow } from "date-fns"
import {
  KeyRoundIcon,
  PencilIcon,
  PlusIcon,
  RefreshCwIcon,
  ShieldOffIcon,
  ToggleLeftIcon,
  ToggleRightIcon,
} from "lucide-react"
import { useCallback, useEffect, useMemo, useState } from "react"
import type {
  ScopeRead,
  ServiceAccountApiKeyCreate,
  ServiceAccountCreate,
  ServiceAccountCreateResponse,
  ServiceAccountRead,
  ServiceAccountScopeRead,
  ServiceAccountUpdate,
} from "@/client"
import { CopyButton } from "@/components/copy-button"
import { AlertNotification } from "@/components/notifications"
import {
  getScopesForLevel,
  ScopeCategoryRow,
} from "@/components/rbac/scope-category-row"
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
import { Input } from "@/components/ui/input"
import { Label } from "@/components/ui/label"
import { toast } from "@/components/ui/use-toast"
import type { TracecatApiError } from "@/lib/errors"
import {
  getCategoryScopes,
  type PermissionLevel,
  RESOURCE_CATEGORIES,
} from "@/lib/rbac"

interface ServiceAccountsManagerProps {
  kindLabel: string
  serviceAccounts: ServiceAccountRead[]
  nextCursor: string | null
  isLoading: boolean
  error: Error | null
  availableScopes: ServiceAccountScopeRead[]
  createPending: boolean
  updatePending: boolean
  disablePending: boolean
  enablePending: boolean
  regeneratePending: boolean
  revokePending: boolean
  showIntroCard?: boolean
  canCreate?: boolean
  canUpdate?: boolean
  canDisable?: boolean
  openCreateSignal?: string | null
  onCreateSignalConsumed?: () => void
  onCreate: (
    requestBody: ServiceAccountCreate
  ) => Promise<ServiceAccountCreateResponse>
  onUpdate: (params: {
    serviceAccountId: string
    requestBody: ServiceAccountUpdate
  }) => Promise<ServiceAccountRead>
  onDisable: (serviceAccountId: string) => Promise<void>
  onEnable: (serviceAccountId: string) => Promise<void>
  onRegenerate: (params: {
    serviceAccountId: string
    requestBody: ServiceAccountApiKeyCreate
  }) => Promise<ServiceAccountCreateResponse>
  onRevoke: (serviceAccountId: string) => Promise<void>
}

interface ServiceAccountDraft {
  name: string
  description: string
  scopeIds: string[]
  initialKeyName: string
}

type ActionTargetType = "disable" | "enable" | "revoke"

const EMPTY_DRAFT: ServiceAccountDraft = {
  name: "",
  description: "",
  scopeIds: [],
  initialKeyName: "Primary",
}

function getApiErrorDetail(error: unknown, fallback: string): string {
  if (typeof error === "object" && error !== null) {
    const apiError = error as TracecatApiError
    if (typeof apiError.body?.detail === "string") {
      return apiError.body.detail
    }
  }
  return fallback
}

function formatTimestamp(value?: string | null): string {
  if (!value) {
    return "Never"
  }

  return formatDistanceToNow(new Date(value), { addSuffix: true })
}

function getStatusLabel(serviceAccount: ServiceAccountRead): string {
  if (serviceAccount.disabled_at) {
    return "Disabled"
  }
  if (serviceAccount.api_key?.revoked_at || !serviceAccount.api_key) {
    return "No active key"
  }
  return "Active"
}

export function ServiceAccountsManager({
  kindLabel,
  serviceAccounts,
  nextCursor,
  isLoading,
  error,
  availableScopes,
  createPending,
  updatePending,
  disablePending,
  enablePending,
  regeneratePending,
  revokePending,
  showIntroCard = true,
  canCreate = true,
  canUpdate = true,
  canDisable = true,
  openCreateSignal,
  onCreateSignalConsumed,
  onCreate,
  onUpdate,
  onDisable,
  onEnable,
  onRegenerate,
  onRevoke,
}: ServiceAccountsManagerProps) {
  const [dialogOpen, setDialogOpen] = useState(false)
  const [editingServiceAccount, setEditingServiceAccount] =
    useState<ServiceAccountRead | null>(null)
  const [actionTarget, setActionTarget] = useState<ServiceAccountRead | null>(
    null
  )
  const [actionType, setActionType] = useState<ActionTargetType | null>(null)
  const [actionDialogOpen, setActionDialogOpen] = useState(false)
  const [rotateTarget, setRotateTarget] = useState<ServiceAccountRead | null>(
    null
  )
  const [rotateKeyName, setRotateKeyName] = useState("Primary")
  const [draft, setDraft] = useState<ServiceAccountDraft>(EMPTY_DRAFT)
  const [createdCredential, setCreatedCredential] =
    useState<ServiceAccountCreateResponse | null>(null)

  useEffect(() => {
    if (!dialogOpen) {
      setEditingServiceAccount(null)
      setDraft(EMPTY_DRAFT)
    }
  }, [dialogOpen])

  useEffect(() => {
    if (!actionDialogOpen) {
      setActionTarget(null)
      setActionType(null)
    }
  }, [actionDialogOpen])

  useEffect(() => {
    if (!openCreateSignal || !canCreate) {
      return
    }
    openCreateDialog()
    onCreateSignalConsumed?.()
  }, [canCreate, onCreateSignalConsumed, openCreateSignal])

  const permissionScopes = useMemo<ScopeRead[]>(
    () =>
      availableScopes.map((scope) => ({
        ...scope,
        source: "platform",
        source_ref: null,
        organization_id: null,
        created_at: "",
        updated_at: "",
      })),
    [availableScopes]
  )

  const selectedScopeIds = useMemo(
    () => new Set(draft.scopeIds),
    [draft.scopeIds]
  )

  const scopeCounts = useMemo(() => {
    let total = 0
    for (const category of Object.values(RESOURCE_CATEGORIES)) {
      const categoryScopes = getCategoryScopes(
        category.resources,
        permissionScopes
      )
      total += categoryScopes.filter((scope) =>
        selectedScopeIds.has(scope.id)
      ).length
    }
    return total
  }, [permissionScopes, selectedScopeIds])

  const isSaving = createPending || updatePending
  const isActionPending =
    disablePending || enablePending || regeneratePending || revokePending

  const openCreateDialog = () => {
    setEditingServiceAccount(null)
    setDraft(EMPTY_DRAFT)
    setDialogOpen(true)
  }

  const openEditDialog = (serviceAccount: ServiceAccountRead) => {
    setEditingServiceAccount(serviceAccount)
    setDraft({
      name: serviceAccount.name,
      description: serviceAccount.description ?? "",
      scopeIds: serviceAccount.scopes?.map((scope) => scope.id) ?? [],
      initialKeyName: serviceAccount.api_key?.name ?? "Primary",
    })
    setDialogOpen(true)
  }

  const openActionDialog = (
    serviceAccount: ServiceAccountRead,
    nextActionType: ActionTargetType
  ) => {
    setActionTarget(serviceAccount)
    setActionType(nextActionType)
    setActionDialogOpen(true)
  }

  const toggleScope = useCallback((scopeId: string, checked: boolean) => {
    setDraft((current) => ({
      ...current,
      scopeIds: checked
        ? [...current.scopeIds, scopeId]
        : current.scopeIds.filter((id) => id !== scopeId),
    }))
  }, [])

  const handleLevelChange = useCallback(
    (categoryResources: string[], level: PermissionLevel) => {
      setDraft((current) => {
        const nextScopeIds = new Set(current.scopeIds)
        const categoryScopes = getCategoryScopes(
          categoryResources,
          permissionScopes
        )

        for (const scope of categoryScopes) {
          nextScopeIds.delete(scope.id)
        }

        if (level !== "none" && level !== "mixed") {
          const scopesToAdd = getScopesForLevel(
            categoryResources,
            permissionScopes,
            level
          )
          for (const scopeId of scopesToAdd) {
            nextScopeIds.add(scopeId)
          }
        }

        return {
          ...current,
          scopeIds: Array.from(nextScopeIds),
        }
      })
    },
    [permissionScopes]
  )

  const handleSave = async () => {
    const trimmedName = draft.name.trim()
    if (!trimmedName) {
      toast({
        title: "Name required",
        description: "Service accounts must have a name.",
        variant: "destructive",
      })
      return
    }

    try {
      if (editingServiceAccount) {
        await onUpdate({
          serviceAccountId: editingServiceAccount.id,
          requestBody: {
            name: trimmedName,
            description: draft.description.trim() || null,
            scope_ids: draft.scopeIds,
          },
        })
        toast({
          title: `${kindLabel} service account updated`,
          description: `${trimmedName} was updated.`,
        })
      } else {
        const response = await onCreate({
          name: trimmedName,
          description: draft.description.trim() || null,
          scope_ids: draft.scopeIds,
          initial_key_name: draft.initialKeyName.trim() || "Primary",
        })
        setCreatedCredential(response)
        toast({
          title: `${kindLabel} service account created`,
          description:
            "Copy the initial API key now. It will not be shown again.",
        })
      }
      setDialogOpen(false)
    } catch (saveError) {
      toast({
        title: `Failed to save ${kindLabel.toLowerCase()} service account`,
        description: getApiErrorDetail(saveError, "Please try again."),
        variant: "destructive",
      })
    }
  }

  const handleConfirmAction = async () => {
    if (!actionTarget || !actionType) {
      return
    }

    try {
      if (actionType === "disable") {
        await onDisable(actionTarget.id)
        toast({
          title: "Service account disabled",
          description: `${actionTarget.name} can no longer authenticate.`,
        })
      } else if (actionType === "enable") {
        await onEnable(actionTarget.id)
        toast({
          title: "Service account enabled",
          description: `${actionTarget.name} can authenticate again.`,
        })
      } else {
        await onRevoke(actionTarget.id)
        toast({
          title: "API key revoked",
          description: `${actionTarget.name} no longer has an active key.`,
        })
      }
      setActionDialogOpen(false)
    } catch (actionError) {
      toast({
        title: "Action failed",
        description: getApiErrorDetail(actionError, "Please try again."),
        variant: "destructive",
      })
    }
  }

  const handleRotate = async () => {
    if (!rotateTarget) {
      return
    }

    try {
      const response = await onRegenerate({
        serviceAccountId: rotateTarget.id,
        requestBody: {
          name: rotateKeyName.trim() || "Primary",
        },
      })
      setCreatedCredential(response)
      setRotateTarget(null)
      setRotateKeyName("Primary")
      toast({
        title: "API key rotated",
        description:
          "Copy the replacement API key now. It will not be shown again.",
      })
    } catch (rotateError) {
      toast({
        title: "Failed to rotate API key",
        description: getApiErrorDetail(rotateError, "Please try again."),
        variant: "destructive",
      })
    }
  }

  if (error) {
    return (
      <AlertNotification
        level="error"
        message={`Error loading ${kindLabel.toLowerCase()} service accounts: ${error.message}`}
      />
    )
  }

  return (
    <div className="space-y-6">
      {showIntroCard ? (
        <div className="flex flex-col gap-3 rounded-lg border p-4 sm:flex-row sm:items-end sm:justify-between">
          <div className="space-y-1">
            <p className="text-sm font-medium">{kindLabel} service accounts</p>
            <p className="text-sm text-muted-foreground">
              Create machine identities and manage their current API keys.
            </p>
          </div>
          <Button onClick={openCreateDialog} disabled={!canCreate}>
            <PlusIcon className="mr-2 size-4" />
            Create service account
          </Button>
        </div>
      ) : null}

      <div className="rounded-lg border">
        {isLoading ? (
          <div className="px-4 py-3 text-sm text-muted-foreground">
            Loading service accounts...
          </div>
        ) : serviceAccounts.length === 0 ? (
          <div className="px-4 py-3 text-sm text-muted-foreground">
            No service accounts created yet.
          </div>
        ) : (
          <div className="divide-y">
            {serviceAccounts.map((serviceAccount) => (
              <div
                key={serviceAccount.id}
                className="group/item flex items-center gap-2 px-4 py-2 transition-colors hover:bg-muted/50"
              >
                <KeyRoundIcon className="size-4 shrink-0 text-primary" />
                <div className="flex min-w-0 flex-1 items-center gap-3">
                  <div className="min-w-0 w-[320px] shrink-0 space-y-0.5">
                    <p className="truncate text-xs font-medium">
                      {serviceAccount.name}
                    </p>
                    <p className="truncate text-xs text-muted-foreground">
                      {serviceAccount.description ||
                        serviceAccount.api_key?.preview ||
                        "No description"}
                    </p>
                  </div>
                  <div className="flex min-w-0 flex-1 items-center gap-2 overflow-hidden">
                    {serviceAccount.api_key ? (
                      <Badge
                        variant="secondary"
                        className="h-5 max-w-[220px] px-2 text-[10px] font-normal"
                      >
                        <span className="truncate font-mono">
                          {serviceAccount.api_key.preview}
                        </span>
                      </Badge>
                    ) : null}
                    {serviceAccount.api_key ? (
                      <Badge
                        variant="secondary"
                        className="h-5 max-w-[160px] px-2 text-[10px] font-normal"
                      >
                        <span className="truncate">
                          {serviceAccount.api_key.name}
                        </span>
                      </Badge>
                    ) : null}
                    <Badge
                      variant={
                        serviceAccount.disabled_at
                          ? "outline"
                          : serviceAccount.api_key
                            ? "secondary"
                            : "outline"
                      }
                      className="h-5 px-2 text-[10px] font-normal"
                    >
                      {getStatusLabel(serviceAccount)}
                    </Badge>
                    <Badge
                      variant="secondary"
                      className="h-5 px-2 text-[10px] font-normal"
                    >
                      Last used {formatTimestamp(serviceAccount.last_used_at)}
                    </Badge>
                    {(serviceAccount.scopes ?? []).slice(0, 2).map((scope) => (
                      <Badge
                        key={scope.id}
                        variant="secondary"
                        className="h-5 max-w-[180px] px-2 text-[10px] font-normal"
                      >
                        <span className="truncate">{scope.name}</span>
                      </Badge>
                    ))}
                    {(serviceAccount.scopes?.length ?? 0) > 2 ? (
                      <Badge
                        variant="outline"
                        className="h-5 px-2 text-[10px] font-normal"
                      >
                        +{(serviceAccount.scopes?.length ?? 0) - 2} more
                      </Badge>
                    ) : null}
                  </div>
                  <div className="ml-auto flex shrink-0 items-center gap-2">
                    <Button
                      variant="outline"
                      size="sm"
                      disabled={!canUpdate}
                      onClick={() => openEditDialog(serviceAccount)}
                    >
                      <PencilIcon className="mr-2 size-4" />
                      Edit
                    </Button>
                    <Button
                      variant="outline"
                      size="sm"
                      disabled={
                        Boolean(serviceAccount.disabled_at) || !canUpdate
                      }
                      onClick={() => {
                        setRotateTarget(serviceAccount)
                        setRotateKeyName(
                          serviceAccount.api_key?.name ?? "Primary"
                        )
                      }}
                    >
                      <RefreshCwIcon className="mr-2 size-4" />
                      Rotate key
                    </Button>
                    <Button
                      variant="ghost"
                      size="sm"
                      className="text-rose-600 hover:text-rose-700"
                      disabled={!serviceAccount.api_key || !canUpdate}
                      onClick={() => openActionDialog(serviceAccount, "revoke")}
                    >
                      <ShieldOffIcon className="mr-2 size-4" />
                      Revoke key
                    </Button>
                    <Button
                      variant="ghost"
                      size="sm"
                      disabled={!canDisable}
                      onClick={() =>
                        openActionDialog(
                          serviceAccount,
                          serviceAccount.disabled_at ? "enable" : "disable"
                        )
                      }
                    >
                      {serviceAccount.disabled_at ? (
                        <ToggleRightIcon className="mr-2 size-4" />
                      ) : (
                        <ToggleLeftIcon className="mr-2 size-4" />
                      )}
                      {serviceAccount.disabled_at ? "Enable" : "Disable"}
                    </Button>
                  </div>
                </div>
              </div>
            ))}
          </div>
        )}
      </div>

      {nextCursor ? (
        <p className="text-xs text-muted-foreground">
          Only the first page of service accounts is shown in this view.
        </p>
      ) : null}

      <Dialog open={dialogOpen} onOpenChange={setDialogOpen}>
        <DialogContent className="max-h-[85vh] max-w-3xl flex flex-col">
          <form
            onSubmit={async (event) => {
              event.preventDefault()
              await handleSave()
            }}
            className="flex flex-1 min-h-0 flex-col"
          >
            <DialogHeader>
              <DialogTitle>
                {editingServiceAccount
                  ? "Edit service account"
                  : "Create service account"}
              </DialogTitle>
              <DialogDescription>
                {editingServiceAccount
                  ? "Update the service account name, description, and permissions."
                  : `Create a ${kindLabel.toLowerCase()} service account with specific permissions.`}
              </DialogDescription>
            </DialogHeader>

            <div className="flex-1 min-h-0 space-y-4 overflow-hidden py-4">
              <div className="grid gap-4 sm:grid-cols-2">
                <div className="space-y-2">
                  <Label htmlFor="service-account-name">
                    Service account name
                  </Label>
                  <Input
                    id="service-account-name"
                    name="service-account-name"
                    value={draft.name}
                    onChange={(event) =>
                      setDraft((current) => ({
                        ...current,
                        name: event.target.value,
                      }))
                    }
                    placeholder="e.g., CI deployer"
                    required
                  />
                </div>
                <div className="space-y-2">
                  <Label htmlFor="service-account-description">
                    Description
                  </Label>
                  <Input
                    id="service-account-description"
                    name="service-account-description"
                    value={draft.description}
                    onChange={(event) =>
                      setDraft((current) => ({
                        ...current,
                        description: event.target.value,
                      }))
                    }
                    placeholder="Optional description"
                  />
                </div>
              </div>

              {!editingServiceAccount ? (
                <div className="space-y-2">
                  <Label htmlFor="service-account-initial-key-name">
                    Initial key label
                  </Label>
                  <Input
                    id="service-account-initial-key-name"
                    name="service-account-initial-key-name"
                    value={draft.initialKeyName}
                    onChange={(event) =>
                      setDraft((current) => ({
                        ...current,
                        initialKeyName: event.target.value,
                      }))
                    }
                    placeholder="Primary"
                  />
                </div>
              ) : null}

              <div className="flex-1 min-h-0 space-y-2">
                <div className="flex items-center justify-between">
                  <Label>Permissions ({scopeCounts} scopes selected)</Label>
                </div>
                <div className="mb-2 text-xs text-muted-foreground">
                  Set permission levels by category, or expand to select
                  individual scopes.
                </div>
                <div className="h-[400px] overflow-y-auto rounded-md border">
                  <div className="divide-y divide-border/50">
                    {Object.entries(RESOURCE_CATEGORIES).map(
                      ([key, category]) => (
                        <ScopeCategoryRow
                          key={key}
                          categoryKey={key}
                          category={category}
                          scopes={permissionScopes}
                          selectedScopeIds={selectedScopeIds}
                          onScopeToggle={toggleScope}
                          onLevelChange={handleLevelChange}
                        />
                      )
                    )}
                  </div>
                </div>
              </div>
            </div>

            <DialogFooter>
              <Button
                type="button"
                variant="outline"
                onClick={() => setDialogOpen(false)}
                disabled={isSaving}
              >
                Cancel
              </Button>
              <Button
                type="submit"
                disabled={
                  isSaving ||
                  (!editingServiceAccount && !canCreate) ||
                  (editingServiceAccount !== null && !canUpdate)
                }
              >
                {isSaving
                  ? "Saving..."
                  : editingServiceAccount
                    ? "Save changes"
                    : "Create service account"}
              </Button>
            </DialogFooter>
          </form>
        </DialogContent>
      </Dialog>

      <Dialog
        open={Boolean(createdCredential)}
        onOpenChange={(open) => {
          if (!open) {
            setCreatedCredential(null)
          }
        }}
      >
        <DialogContent className="max-w-md">
          <DialogHeader>
            <DialogTitle>Copy API key</DialogTitle>
            <DialogDescription>
              This secret is only shown once. Copy it now before closing this
              dialog.
            </DialogDescription>
          </DialogHeader>
          {createdCredential ? (
            <div className="space-y-4">
              <div className="space-y-2">
                <Label htmlFor="service-account-key-preview">Key preview</Label>
                <div className="flex items-center gap-2">
                  <Input
                    id="service-account-key-preview"
                    name="service-account-key-preview"
                    value={
                      createdCredential.service_account.api_key?.preview ??
                      "Unavailable"
                    }
                    readOnly
                    disabled
                    className="select-none font-mono text-xs"
                  />
                  <CopyButton
                    value={createdCredential.api_key}
                    toastMessage="API key copied"
                    tooltipMessage="Copy raw key"
                  />
                </div>
              </div>
            </div>
          ) : null}
          <DialogFooter>
            <Button
              variant="outline"
              onClick={() => setCreatedCredential(null)}
            >
              Close
            </Button>
          </DialogFooter>
        </DialogContent>
      </Dialog>

      <Dialog
        open={Boolean(rotateTarget)}
        onOpenChange={(open) => !open && setRotateTarget(null)}
      >
        <DialogContent>
          <DialogHeader>
            <DialogTitle>Rotate API key</DialogTitle>
            <DialogDescription>
              The current key will be revoked and replaced with a new one.
            </DialogDescription>
          </DialogHeader>
          <div className="space-y-2">
            <Label htmlFor="rotated-key-name">New key label</Label>
            <Input
              id="rotated-key-name"
              name="rotated-key-name"
              value={rotateKeyName}
              onChange={(event) => setRotateKeyName(event.target.value)}
              placeholder="Primary"
            />
          </div>
          <DialogFooter>
            <Button
              variant="outline"
              onClick={() => setRotateTarget(null)}
              disabled={regeneratePending}
            >
              Cancel
            </Button>
            <Button onClick={handleRotate} disabled={regeneratePending}>
              {regeneratePending ? "Rotating..." : "Rotate key"}
            </Button>
          </DialogFooter>
        </DialogContent>
      </Dialog>

      <AlertDialog
        open={actionDialogOpen}
        onOpenChange={(open) => {
          setActionDialogOpen(open)
        }}
      >
        <AlertDialogContent>
          <AlertDialogHeader>
            <AlertDialogTitle>
              {actionType === "disable"
                ? "Disable service account"
                : actionType === "enable"
                  ? "Enable service account"
                  : "Revoke API key"}
            </AlertDialogTitle>
            <AlertDialogDescription>
              {actionType === "disable"
                ? `${actionTarget?.name} will stop authenticating immediately.`
                : actionType === "enable"
                  ? `${actionTarget?.name} will be able to authenticate again.`
                  : `${actionTarget?.name} will lose its current active API key.`}
            </AlertDialogDescription>
          </AlertDialogHeader>
          <AlertDialogFooter>
            <AlertDialogCancel disabled={isActionPending}>
              Cancel
            </AlertDialogCancel>
            <AlertDialogAction
              disabled={isActionPending}
              onClick={async (event) => {
                event.preventDefault()
                await handleConfirmAction()
              }}
            >
              {isActionPending ? "Working..." : "Confirm"}
            </AlertDialogAction>
          </AlertDialogFooter>
        </AlertDialogContent>
      </AlertDialog>
    </div>
  )
}
