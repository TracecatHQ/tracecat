"use client"

import { useQuery } from "@tanstack/react-query"
import { formatDistanceToNow } from "date-fns"
import {
  ChevronRightIcon,
  CircleCheckIcon,
  CircleXIcon,
  KeyRoundIcon,
  PencilIcon,
  RefreshCwIcon,
  SearchIcon,
  ShieldIcon,
} from "lucide-react"
import { useCallback, useEffect, useMemo, useState } from "react"
import type {
  ScopeRead,
  ServiceAccountApiKeyCreate,
  ServiceAccountApiKeyIssueResponse,
  ServiceAccountApiKeyRead,
  ServiceAccountCreate,
  ServiceAccountRead,
  ServiceAccountScopeRead,
  ServiceAccountUpdate,
} from "@/client"
import { CasePanelSection } from "@/components/cases/case-panel-section"
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
  Table,
  TableBody,
  TableCell,
  TableHead,
  TableHeader,
  TableRow,
} from "@/components/ui/table"
import {
  Tooltip,
  TooltipContent,
  TooltipTrigger,
} from "@/components/ui/tooltip"
import { toast } from "@/components/ui/use-toast"
import type { TracecatApiError } from "@/lib/errors"
import {
  getCategoryScopes,
  type PermissionLevel,
  RESOURCE_CATEGORIES,
} from "@/lib/rbac"
import { cn } from "@/lib/utils"

interface ServiceAccountApiKeysPage {
  items?: ServiceAccountApiKeyRead[]
  next_cursor?: string | null
}

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
  issueApiKeyPending: boolean
  revokeApiKeyPending: boolean
  apiKeysQueryKeyPrefix: readonly unknown[]
  canCreate?: boolean
  canUpdate?: boolean
  canDisable?: boolean
  openCreateSignal?: string | null
  onCreateSignalConsumed?: () => void
  onCreate: (
    requestBody: ServiceAccountCreate
  ) => Promise<ServiceAccountApiKeyIssueResponse>
  onUpdate: (params: {
    serviceAccountId: string
    requestBody: ServiceAccountUpdate
  }) => Promise<ServiceAccountRead>
  onDisable: (serviceAccountId: string) => Promise<void>
  onEnable: (serviceAccountId: string) => Promise<void>
  onIssueApiKey: (params: {
    serviceAccountId: string
    requestBody: ServiceAccountApiKeyCreate
  }) => Promise<ServiceAccountApiKeyIssueResponse>
  onRevokeApiKey: (params: {
    serviceAccountId: string
    apiKeyId: string
  }) => Promise<void>
  listApiKeys: (serviceAccountId: string) => Promise<ServiceAccountApiKeysPage>
}

interface ServiceAccountDraft {
  name: string
  description: string
  scopeIds: string[]
  initialKeyName: string
}

type ServiceAccountStatusFilter =
  | "all"
  | "active"
  | "disabled"
  | "no-active-key"

type ActionDialogState =
  | {
      type: "disable"
      serviceAccount: ServiceAccountRead
    }
  | {
      type: "enable"
      serviceAccount: ServiceAccountRead
    }
  | {
      type: "revoke-api-key"
      serviceAccount: ServiceAccountRead
      apiKey: ServiceAccountApiKeyRead
    }

const EMPTY_DRAFT: ServiceAccountDraft = {
  name: "",
  description: "",
  scopeIds: [],
  initialKeyName: "Primary",
}

type ServiceAccountStatusGroup = "active" | "disabled" | "no_active_key"

interface StatusGroupConfig {
  label: string
  dotClassName: string
  textClassName: string
}

const STATUS_GROUPS: Record<ServiceAccountStatusGroup, StatusGroupConfig> = {
  active: {
    label: "Active",
    dotClassName: "bg-green-500",
    textClassName: "text-green-700 dark:text-green-400",
  },
  disabled: {
    label: "Disabled",
    dotClassName: "bg-muted-foreground",
    textClassName: "text-muted-foreground",
  },
  no_active_key: {
    label: "No active key",
    dotClassName: "bg-rose-500",
    textClassName: "text-rose-700 dark:text-rose-400",
  },
}

function getServiceAccountGroup(
  serviceAccount: ServiceAccountRead
): ServiceAccountStatusGroup {
  if (serviceAccount.disabled_at) return "disabled"
  if (!serviceAccount.active_api_key) return "no_active_key"
  return "active"
}

const API_KEY_PREVIEW_LENGTH = 4

const SERVICE_ACCOUNT_STATUS_FILTERS: Array<{
  value: ServiceAccountStatusFilter
  label: string
}> = [
  { value: "all", label: "All" },
  { value: "active", label: "Active" },
  { value: "disabled", label: "Disabled" },
  { value: "no-active-key", label: "No active key" },
]

const SERVICE_ACCOUNT_NAME_COLUMN_CLASS =
  "min-w-0 w-[280px] shrink-0 truncate text-xs font-medium"
const INLINE_NAME_HIGHLIGHT_CLASS =
  "mx-1 inline-flex rounded bg-secondary px-1.5 py-0.5 align-middle font-mono text-xs font-normal text-foreground"

function deriveApiKeyPreview(rawKey: string): string {
  const prefixMatch = rawKey.match(/^(tc_(?:org_|ws_)?sk_)/)
  const prefix = prefixMatch?.[1]
  if (!prefix || rawKey.length < prefix.length + API_KEY_PREVIEW_LENGTH) {
    return rawKey
  }
  return `${prefix}...${rawKey.slice(-API_KEY_PREVIEW_LENGTH)}`
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

function getApiKeyStatusLabel(apiKey: ServiceAccountApiKeyRead): string {
  return apiKey.revoked_at ? "Revoked" : "Active"
}

function getApiKeyStatusTone(apiKey: ServiceAccountApiKeyRead): {
  dotClassName: string
  textClassName: string
} {
  return apiKey.revoked_at
    ? {
        dotClassName: "bg-rose-500",
        textClassName: "text-rose-700 dark:text-rose-400",
      }
    : {
        dotClassName: "bg-green-500",
        textClassName: "text-green-700 dark:text-green-400",
      }
}

function matchesServiceAccountStatusFilter(
  serviceAccount: ServiceAccountRead,
  statusFilter: ServiceAccountStatusFilter
): boolean {
  switch (statusFilter) {
    case "all":
      return true
    case "active":
      return (
        serviceAccount.disabled_at === null &&
        serviceAccount.active_api_key !== null &&
        serviceAccount.active_api_key !== undefined
      )
    case "disabled":
      return serviceAccount.disabled_at !== null
    case "no-active-key":
      return (
        serviceAccount.disabled_at === null && !serviceAccount.active_api_key
      )
  }
}

function matchesServiceAccountSearchQuery(
  serviceAccount: ServiceAccountRead,
  searchQuery: string
): boolean {
  const normalizedQuery = searchQuery.trim().toLowerCase()
  if (!normalizedQuery) {
    return true
  }

  const searchableParts = [
    serviceAccount.name,
    serviceAccount.description,
    serviceAccount.active_api_key?.name,
    serviceAccount.active_api_key?.preview,
    ...(serviceAccount.scopes?.map((scope) => scope.name) ?? []),
  ]

  return searchableParts.some((part) =>
    part?.toLowerCase().includes(normalizedQuery)
  )
}

function RowActionButton({
  label,
  disabled = false,
  onClick,
  danger = false,
  className,
  children,
}: {
  label: string
  disabled?: boolean
  onClick: () => void
  danger?: boolean
  className?: string
  children: React.ReactNode
}) {
  return (
    <Tooltip>
      <TooltipTrigger asChild>
        <Button
          variant="ghost"
          size="icon"
          className={cn(
            danger
              ? "size-8 text-rose-600 hover:text-rose-700"
              : "size-8 text-muted-foreground hover:text-foreground",
            className
          )}
          disabled={disabled}
          onClick={onClick}
          aria-label={label}
        >
          {children}
          <span className="sr-only">{label}</span>
        </Button>
      </TooltipTrigger>
      <TooltipContent>{label}</TooltipContent>
    </Tooltip>
  )
}

function sortServiceAccountScopes(
  scopes: ServiceAccountScopeRead[]
): ServiceAccountScopeRead[] {
  return [...scopes].sort((left, right) => left.name.localeCompare(right.name))
}

function ServiceAccountDetailPanel({
  serviceAccount,
  expanded,
  listApiKeys,
  apiKeysQueryKeyPrefix,
  canUpdate,
  revokeApiKeyPending,
  onOpenActionDialog,
}: {
  serviceAccount: ServiceAccountRead
  expanded: boolean
  listApiKeys: (serviceAccountId: string) => Promise<ServiceAccountApiKeysPage>
  apiKeysQueryKeyPrefix: readonly unknown[]
  canUpdate: boolean
  revokeApiKeyPending: boolean
  onOpenActionDialog: (state: ActionDialogState) => void
}) {
  const [permissionsOpen, setPermissionsOpen] = useState(true)
  const [apiKeysOpen, setApiKeysOpen] = useState(true)
  const {
    data: apiKeysPage,
    isLoading,
    error,
  } = useQuery({
    queryKey: [...apiKeysQueryKeyPrefix, serviceAccount.id, "api-keys"],
    queryFn: async () => await listApiKeys(serviceAccount.id),
    enabled: expanded,
  })

  if (!expanded) {
    return null
  }

  return (
    <div className="border-t bg-background px-4 py-4">
      <div className="flex flex-col gap-6">
        <CasePanelSection
          title="Scopes"
          titleNode={
            <span className="inline-flex items-center gap-2">
              <ShieldIcon className="size-3.5 text-muted-foreground" />
              <span>Scopes</span>
            </span>
          }
          isOpen={permissionsOpen}
          onOpenChange={setPermissionsOpen}
        >
          {(serviceAccount.scopes?.length ?? 0) > 0 ? (
            <div className="flex flex-wrap gap-2">
              {sortServiceAccountScopes(serviceAccount.scopes ?? []).map(
                (scope) => (
                  <Badge
                    key={scope.id}
                    variant="secondary"
                    className="px-2 py-0.5 font-mono text-[10px] font-normal text-muted-foreground"
                  >
                    {scope.name}
                  </Badge>
                )
              )}
            </div>
          ) : (
            <p className="text-xs text-muted-foreground">No scopes assigned.</p>
          )}
        </CasePanelSection>

        <CasePanelSection
          title="API keys"
          titleNode={
            <span className="inline-flex items-center gap-2">
              <KeyRoundIcon className="size-3.5 text-muted-foreground" />
              <span>API keys</span>
            </span>
          }
          isOpen={apiKeysOpen}
          onOpenChange={setApiKeysOpen}
        >
          {error ? (
            <AlertNotification
              level="error"
              message={`Error loading API keys: ${error.message}`}
            />
          ) : isLoading ? (
            <div className="text-xs text-muted-foreground">
              Loading API keys...
            </div>
          ) : (apiKeysPage?.items?.length ?? 0) === 0 ? (
            <Empty className="border-0 px-0">
              <EmptyHeader>
                <EmptyMedia variant="icon">
                  <KeyRoundIcon />
                </EmptyMedia>
                <EmptyTitle>No API keys</EmptyTitle>
                <EmptyDescription>
                  This service account does not have any issued keys yet.
                </EmptyDescription>
              </EmptyHeader>
            </Empty>
          ) : (
            <div className="rounded-md border bg-background">
              <Table>
                <TableHeader>
                  <TableRow>
                    <TableHead className="text-xs">Key</TableHead>
                    <TableHead className="text-xs">Status</TableHead>
                    <TableHead className="text-xs">Created</TableHead>
                    <TableHead className="text-xs">Last used</TableHead>
                    <TableHead className="h-8 w-[40px]" />
                  </TableRow>
                </TableHeader>
                <TableBody>
                  {apiKeysPage?.items?.map((apiKey) => {
                    const statusTone = getApiKeyStatusTone(apiKey)

                    return (
                      <TableRow key={apiKey.id}>
                        <TableCell>
                          <div className="flex min-w-0 items-center gap-2">
                            <span className="truncate text-xs font-medium">
                              {apiKey.name}
                            </span>
                            <span className="truncate font-mono text-xs text-muted-foreground">
                              {apiKey.preview}
                            </span>
                          </div>
                        </TableCell>
                        <TableCell>
                          <Badge
                            variant="secondary"
                            className={cn(
                              "h-5 gap-1.5 bg-secondary px-2 text-[10px] font-normal",
                              statusTone.textClassName
                            )}
                          >
                            <span
                              className={cn(
                                "size-1.5 rounded-full",
                                statusTone.dotClassName
                              )}
                            />
                            {getApiKeyStatusLabel(apiKey)}
                          </Badge>
                        </TableCell>
                        <TableCell className="text-xs text-muted-foreground">
                          {formatTimestamp(apiKey.created_at)}
                        </TableCell>
                        <TableCell className="text-xs text-muted-foreground">
                          {formatTimestamp(apiKey.last_used_at)}
                        </TableCell>
                        <TableCell className="text-right">
                          <div className="flex items-center justify-end gap-1">
                            <RowActionButton
                              label="Revoke API key"
                              danger={true}
                              className="size-7"
                              disabled={
                                revokeApiKeyPending ||
                                Boolean(apiKey.revoked_at) ||
                                !canUpdate
                              }
                              onClick={() =>
                                onOpenActionDialog({
                                  type: "revoke-api-key",
                                  serviceAccount,
                                  apiKey,
                                })
                              }
                            >
                              <CircleXIcon className="size-3.5 text-destructive" />
                            </RowActionButton>
                          </div>
                        </TableCell>
                      </TableRow>
                    )
                  })}
                </TableBody>
              </Table>
            </div>
          )}

          {apiKeysPage?.next_cursor ? (
            <p className="mt-4 text-xs text-muted-foreground">
              Only the first page of API keys is shown in this view.
            </p>
          ) : null}
        </CasePanelSection>
      </div>
    </div>
  )
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
  issueApiKeyPending,
  revokeApiKeyPending,
  apiKeysQueryKeyPrefix,
  canCreate = true,
  canUpdate = true,
  canDisable = true,
  openCreateSignal,
  onCreateSignalConsumed,
  onCreate,
  onUpdate,
  onDisable,
  onEnable,
  onIssueApiKey,
  onRevokeApiKey,
  listApiKeys,
}: ServiceAccountsManagerProps) {
  const [dialogOpen, setDialogOpen] = useState(false)
  const [editingServiceAccount, setEditingServiceAccount] =
    useState<ServiceAccountRead | null>(null)
  const [actionDialogState, setActionDialogState] =
    useState<ActionDialogState | null>(null)
  const [issueTarget, setIssueTarget] = useState<ServiceAccountRead | null>(
    null
  )
  const [issueKeyName, setIssueKeyName] = useState("Primary")
  const [draft, setDraft] = useState<ServiceAccountDraft>(EMPTY_DRAFT)
  const [issuedCredential, setIssuedCredential] =
    useState<ServiceAccountApiKeyIssueResponse | null>(null)
  const [searchQuery, setSearchQuery] = useState("")
  const [statusFilter, setStatusFilter] =
    useState<ServiceAccountStatusFilter>("all")
  const [expandedServiceAccountId, setExpandedServiceAccountId] = useState<
    string | null
  >(null)

  useEffect(() => {
    if (!dialogOpen) {
      setEditingServiceAccount(null)
      setDraft(EMPTY_DRAFT)
    }
  }, [dialogOpen])

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
    disablePending || enablePending || revokeApiKeyPending || issueApiKeyPending
  const filteredServiceAccounts = useMemo(
    () =>
      serviceAccounts.filter(
        (serviceAccount) =>
          matchesServiceAccountStatusFilter(serviceAccount, statusFilter) &&
          matchesServiceAccountSearchQuery(serviceAccount, searchQuery)
      ),
    [searchQuery, serviceAccounts, statusFilter]
  )

  const hasActiveFilters =
    searchQuery.trim().length > 0 || statusFilter !== "all"

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
      initialKeyName: serviceAccount.active_api_key?.name ?? "Primary",
    })
    setDialogOpen(true)
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

  async function handleSave() {
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
        setIssuedCredential(response)
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

  async function handleConfirmAction() {
    if (!actionDialogState) {
      return
    }

    try {
      if (actionDialogState.type === "disable") {
        await onDisable(actionDialogState.serviceAccount.id)
        toast({
          title: "Service account disabled",
          description: `${actionDialogState.serviceAccount.name} can no longer authenticate.`,
        })
      } else if (actionDialogState.type === "enable") {
        await onEnable(actionDialogState.serviceAccount.id)
        toast({
          title: "Service account enabled",
          description: `${actionDialogState.serviceAccount.name} can authenticate again.`,
        })
      } else {
        await onRevokeApiKey({
          serviceAccountId: actionDialogState.serviceAccount.id,
          apiKeyId: actionDialogState.apiKey.id,
        })
        toast({
          title: "API key revoked",
          description: `${actionDialogState.apiKey.name} was revoked.`,
        })
      }
      setActionDialogState(null)
    } catch (actionError) {
      toast({
        title: "Action failed",
        description: getApiErrorDetail(actionError, "Please try again."),
        variant: "destructive",
      })
    }
  }

  async function handleIssueApiKey() {
    if (!issueTarget) {
      return
    }

    try {
      const response = await onIssueApiKey({
        serviceAccountId: issueTarget.id,
        requestBody: {
          name: issueKeyName.trim() || "Primary",
        },
      })
      setIssuedCredential(response)
      setIssueTarget(null)
      setIssueKeyName("Primary")
      toast({
        title: "API key issued",
        description:
          "Copy the replacement API key now. It will not be shown again.",
      })
    } catch (issueError) {
      toast({
        title: "Failed to issue API key",
        description: getApiErrorDetail(issueError, "Please try again."),
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
    <div className="flex size-full flex-col">
      <div className="shrink-0">
        <header className="flex h-10 items-center gap-3 border-b pl-3 pr-4">
          <div className="flex min-w-0 items-center gap-3">
            <div className="flex size-7 shrink-0 items-center justify-center">
              <SearchIcon className="size-4 text-muted-foreground" />
            </div>
            <Input
              type="text"
              placeholder={`Search ${kindLabel.toLowerCase()} service accounts...`}
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
              {filteredServiceAccounts.length} service accounts
            </span>
          </div>
        </header>

        <div className="flex flex-wrap items-center gap-2 border-b px-4 py-2">
          {SERVICE_ACCOUNT_STATUS_FILTERS.map((filterOption) => (
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

          {hasActiveFilters && (
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
          )}
        </div>
      </div>

      <div className="min-h-0 flex-1 overflow-auto">
        {isLoading ? (
          <div className="px-4 py-3 text-sm text-muted-foreground">
            Loading service accounts...
          </div>
        ) : serviceAccounts.length === 0 ? (
          <Empty className="rounded-none border-0">
            <EmptyHeader>
              <EmptyMedia variant="icon">
                <KeyRoundIcon />
              </EmptyMedia>
              <EmptyTitle>No service accounts</EmptyTitle>
              <EmptyDescription>
                Create a service account to manage machine access for this{" "}
                {kindLabel.toLowerCase()}.
              </EmptyDescription>
            </EmptyHeader>
          </Empty>
        ) : filteredServiceAccounts.length === 0 ? (
          <Empty className="rounded-none border-0">
            <EmptyHeader>
              <EmptyMedia variant="icon">
                <SearchIcon />
              </EmptyMedia>
              <EmptyTitle>No matching service accounts</EmptyTitle>
              <EmptyDescription>
                Adjust the search query or filters to find a service account.
              </EmptyDescription>
            </EmptyHeader>
          </Empty>
        ) : (
          <div className="divide-y divide-border/50">
            {filteredServiceAccounts.map((serviceAccount) => {
              const isExpanded = expandedServiceAccountId === serviceAccount.id
              const statusGroup = getServiceAccountGroup(serviceAccount)
              const statusConfig = STATUS_GROUPS[statusGroup]

              return (
                <div key={serviceAccount.id}>
                  <div
                    className={cn(
                      "group/item flex cursor-pointer items-center gap-3 px-3 py-2 text-left transition-colors",
                      "hover:bg-muted/50",
                      isExpanded && "bg-muted/30"
                    )}
                    role="button"
                    tabIndex={0}
                    onClick={() =>
                      setExpandedServiceAccountId(
                        isExpanded ? null : serviceAccount.id
                      )
                    }
                    onKeyDown={(event) => {
                      if (event.key === "Enter" || event.key === " ") {
                        event.preventDefault()
                        setExpandedServiceAccountId(
                          isExpanded ? null : serviceAccount.id
                        )
                      }
                    }}
                    aria-expanded={isExpanded}
                  >
                    <div className="flex h-7 w-7 shrink-0 items-center justify-center">
                      <ChevronRightIcon
                        className={cn(
                          "size-4 text-muted-foreground transition-transform duration-200",
                          isExpanded && "rotate-90"
                        )}
                      />
                    </div>

                    <div className="flex min-w-0 flex-1 items-center gap-3">
                      <div className="flex min-w-0 flex-1 items-center gap-2">
                        <KeyRoundIcon className="size-4 shrink-0 text-muted-foreground" />
                        <span className={SERVICE_ACCOUNT_NAME_COLUMN_CLASS}>
                          {serviceAccount.name}
                        </span>
                        <div className="flex min-w-0 flex-1 items-center justify-start gap-2 overflow-hidden">
                          <Badge
                            variant="secondary"
                            className={cn(
                              "h-5 shrink-0 gap-1.5 bg-secondary px-1.5 py-0 text-[10px] font-normal",
                              statusConfig.textClassName
                            )}
                          >
                            <span
                              className={cn(
                                "size-1.5 shrink-0 rounded-full",
                                statusConfig.dotClassName
                              )}
                            />
                            <span>{statusConfig.label}</span>
                          </Badge>
                          {serviceAccount.description ? (
                            <span className="truncate text-xs text-muted-foreground">
                              {serviceAccount.description}
                            </span>
                          ) : null}
                        </div>
                      </div>

                      <div className="flex shrink-0 items-center gap-2">
                        <Badge
                          variant="secondary"
                          className="h-5 shrink-0 gap-1 px-1.5 py-0 text-[10px] font-normal"
                        >
                          <ShieldIcon className="size-3 shrink-0 text-muted-foreground" />
                          {(serviceAccount.scopes?.length ?? 0).toString()}
                        </Badge>
                        <Badge
                          variant="secondary"
                          className="h-5 shrink-0 gap-1 px-1.5 py-0 text-[10px] font-normal"
                        >
                          <KeyRoundIcon className="size-3 shrink-0 text-muted-foreground" />
                          {(
                            serviceAccount.api_key_counts?.total ?? 0
                          ).toString()}
                        </Badge>
                        <span className="text-xs text-muted-foreground">
                          {formatTimestamp(serviceAccount.last_used_at)}
                        </span>
                      </div>
                    </div>

                    <div
                      className="flex shrink-0 items-center gap-1 opacity-0 transition-opacity group-hover/item:opacity-100"
                      onClick={(event) => event.stopPropagation()}
                      onKeyDown={(event) => event.stopPropagation()}
                    >
                      <RowActionButton
                        label="Edit service account"
                        className="size-7"
                        disabled={!canUpdate}
                        onClick={() => openEditDialog(serviceAccount)}
                      >
                        <PencilIcon className="size-3.5" />
                      </RowActionButton>
                      <RowActionButton
                        label="Issue new API key"
                        className="size-7"
                        disabled={
                          Boolean(serviceAccount.disabled_at) || !canUpdate
                        }
                        onClick={() => {
                          setIssueTarget(serviceAccount)
                          setIssueKeyName(
                            serviceAccount.active_api_key?.name ?? "Primary"
                          )
                        }}
                      >
                        <RefreshCwIcon className="size-3.5" />
                      </RowActionButton>
                      <RowActionButton
                        label={
                          serviceAccount.disabled_at
                            ? "Enable service account"
                            : "Disable service account"
                        }
                        className="size-7"
                        disabled={!canDisable}
                        onClick={() =>
                          setActionDialogState({
                            type: serviceAccount.disabled_at
                              ? "enable"
                              : "disable",
                            serviceAccount,
                          })
                        }
                      >
                        {serviceAccount.disabled_at ? (
                          <CircleCheckIcon className="size-3.5 text-green-600" />
                        ) : (
                          <CircleXIcon className="size-3.5 text-destructive" />
                        )}
                      </RowActionButton>
                    </div>
                  </div>

                  {isExpanded && (
                    <ServiceAccountDetailPanel
                      serviceAccount={serviceAccount}
                      expanded={isExpanded}
                      listApiKeys={listApiKeys}
                      apiKeysQueryKeyPrefix={apiKeysQueryKeyPrefix}
                      canUpdate={canUpdate}
                      revokeApiKeyPending={revokeApiKeyPending}
                      onOpenActionDialog={setActionDialogState}
                    />
                  )}
                </div>
              )
            })}
          </div>
        )}

        {nextCursor ? (
          <div className="flex h-10 items-center justify-center text-xs text-muted-foreground">
            Only the first page of service accounts is shown.
          </div>
        ) : null}
      </div>

      <Dialog open={dialogOpen} onOpenChange={setDialogOpen}>
        <DialogContent className="flex max-h-[85vh] max-w-3xl flex-col">
          <form
            onSubmit={async (event) => {
              event.preventDefault()
              await handleSave()
            }}
            className="flex min-h-0 flex-1 flex-col"
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

            <div className="flex min-h-0 flex-1 flex-col gap-4 overflow-hidden py-4">
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

              <div className="flex min-h-0 flex-1 flex-col gap-2">
                <div className="flex items-center justify-between">
                  <Label>Permissions ({scopeCounts} scopes selected)</Label>
                </div>
                <div className="text-xs text-muted-foreground">
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
        open={Boolean(issuedCredential)}
        onOpenChange={(open) => {
          if (!open) {
            setIssuedCredential(null)
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
          {issuedCredential ? (
            <div className="space-y-4">
              <div className="space-y-2">
                <Label htmlFor="service-account-key-preview">Key preview</Label>
                <div className="flex items-center gap-2">
                  <Input
                    id="service-account-key-preview"
                    name="service-account-key-preview"
                    value={
                      issuedCredential.issued_api_key.api_key.preview ??
                      deriveApiKeyPreview(
                        issuedCredential.issued_api_key.raw_key
                      )
                    }
                    readOnly={true}
                    disabled={true}
                    className="select-none font-mono text-xs"
                  />
                  <CopyButton
                    value={issuedCredential.issued_api_key.raw_key}
                    toastMessage="API key copied"
                    tooltipMessage="Copy raw key"
                  />
                </div>
              </div>
            </div>
          ) : null}
          <DialogFooter>
            <Button variant="outline" onClick={() => setIssuedCredential(null)}>
              Close
            </Button>
          </DialogFooter>
        </DialogContent>
      </Dialog>

      <Dialog
        open={Boolean(issueTarget)}
        onOpenChange={(open) => !open && setIssueTarget(null)}
      >
        <DialogContent>
          <DialogHeader>
            <DialogTitle>Issue new API key</DialogTitle>
            <DialogDescription>
              {issueTarget?.active_api_key?.name ? (
                <>
                  The current active key{" "}
                  <span className={INLINE_NAME_HIGHLIGHT_CLASS}>
                    {issueTarget.active_api_key.name}
                  </span>
                  will be revoked and replaced with a new one.
                </>
              ) : (
                "The current active key will be revoked and replaced with a new one."
              )}
            </DialogDescription>
          </DialogHeader>
          <div className="space-y-2">
            <Label htmlFor="issued-key-name">New key label</Label>
            <Input
              id="issued-key-name"
              name="issued-key-name"
              value={issueKeyName}
              onChange={(event) => setIssueKeyName(event.target.value)}
              placeholder="Primary"
            />
          </div>
          <DialogFooter>
            <Button
              variant="outline"
              onClick={() => setIssueTarget(null)}
              disabled={issueApiKeyPending}
            >
              Cancel
            </Button>
            <Button onClick={handleIssueApiKey} disabled={issueApiKeyPending}>
              {issueApiKeyPending ? "Issuing..." : "Issue key"}
            </Button>
          </DialogFooter>
        </DialogContent>
      </Dialog>

      <AlertDialog
        open={Boolean(actionDialogState)}
        onOpenChange={(open) => {
          if (!open) {
            setActionDialogState(null)
          }
        }}
      >
        <AlertDialogContent>
          <AlertDialogHeader>
            <AlertDialogTitle>
              {actionDialogState?.type === "disable"
                ? "Disable service account"
                : actionDialogState?.type === "enable"
                  ? "Enable service account"
                  : "Revoke API key"}
            </AlertDialogTitle>
            <AlertDialogDescription>
              {actionDialogState?.type === "disable" ? (
                <>
                  <span className={INLINE_NAME_HIGHLIGHT_CLASS}>
                    {actionDialogState.serviceAccount.name}
                  </span>
                  will stop authenticating immediately.
                </>
              ) : actionDialogState?.type === "enable" ? (
                <>
                  <span className={INLINE_NAME_HIGHLIGHT_CLASS}>
                    {actionDialogState.serviceAccount.name}
                  </span>
                  will be able to authenticate again.
                </>
              ) : (
                <>
                  <span className={INLINE_NAME_HIGHLIGHT_CLASS}>
                    {actionDialogState?.apiKey.name}
                  </span>
                  will no longer authenticate.
                </>
              )}
            </AlertDialogDescription>
          </AlertDialogHeader>
          <AlertDialogFooter>
            <AlertDialogCancel disabled={isActionPending}>
              Cancel
            </AlertDialogCancel>
            <AlertDialogAction
              variant={
                actionDialogState?.type === "enable" ? "default" : "destructive"
              }
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
