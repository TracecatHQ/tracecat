"use client"

import { DotsHorizontalIcon } from "@radix-ui/react-icons"
import { useQueries } from "@tanstack/react-query"
import { format } from "date-fns"
import {
  ChevronRight,
  Clock3,
  GitCommitHorizontal,
  HistoryIcon,
} from "lucide-react"
import { useEffect, useMemo, useState } from "react"
import type {
  RegistryActionReadMinimal,
  RegistryRepositoryReadMinimal,
  RepositoryStatus,
  tracecat__admin__registry__schemas__RegistryVersionRead,
} from "@/client"
import { adminRegistryListRegistryVersions } from "@/client"
import { useScopeCheck } from "@/components/auth/scope-guard"
import {
  CatalogHeader,
  type CatalogHeaderSelectFilter,
} from "@/components/catalog/catalog-header"
import { getIcon } from "@/components/icons"
import { CenteredSpinner } from "@/components/loading/spinner"
import { AlertNotification } from "@/components/notifications"
import { actionTypeToLabel } from "@/components/registry/icons"
import {
  getCustomRegistryRepository,
  getRegistryOriginLabel,
  isTracecatRegistryOrigin,
  shortCommitSha,
} from "@/components/registry/utils"
import { Badge } from "@/components/ui/badge"
import { Button } from "@/components/ui/button"
import {
  Collapsible,
  CollapsibleContent,
  CollapsibleTrigger,
} from "@/components/ui/collapsible"
import {
  DropdownMenu,
  DropdownMenuContent,
  DropdownMenuItem,
  DropdownMenuTrigger,
} from "@/components/ui/dropdown-menu"
import {
  Item,
  ItemActions,
  ItemContent,
  ItemDescription,
  ItemMedia,
  ItemTitle,
} from "@/components/ui/item"
import { ScrollArea } from "@/components/ui/scroll-area"
import {
  Tooltip,
  TooltipContent,
  TooltipTrigger,
} from "@/components/ui/tooltip"
import { toast } from "@/components/ui/use-toast"
import { useAdminRegistryStatus } from "@/hooks/use-admin"
import { useAuth } from "@/hooks/use-auth"
import { useRegistryActions, useRegistryRepositories } from "@/lib/hooks"
import { copyToClipboard } from "@/lib/utils"

type ActionTypeFilter = RegistryActionReadMinimal["type"] | "all"
type ActionSortField = "name" | "action" | "namespace"
type ActionSortDirection = "asc" | "desc"
type TracecatVersionState =
  | { status: "ready"; version: string }
  | { status: "loading" }
  | { status: "truncated" }
  | { status: "unavailable" }
  | { status: "error" }

const PLATFORM_VERSION_FETCH_LIMIT = 200

type RegistryActionGroup = {
  origin: string
  label: string
  kind: "tracecat" | "custom" | "other"
  actions: RegistryActionReadMinimal[]
  metadata: RegistryRepositoryReadMinimal | RepositoryStatus | null
  currentVersionState?: TracecatVersionState | null
}

function buildSearchText(action: RegistryActionReadMinimal): string {
  return [
    action.default_title,
    action.name,
    action.description,
    action.namespace,
    action.action,
  ]
    .filter(Boolean)
    .join(" ")
    .toLowerCase()
}

function getActionDisplayName(action: RegistryActionReadMinimal): string {
  return action.default_title ?? action.name
}

function getActionSortValue(
  action: RegistryActionReadMinimal,
  field: ActionSortField
): string {
  switch (field) {
    case "name":
      return getActionDisplayName(action)
    case "namespace":
      return action.namespace
    case "action":
    default:
      return action.action
  }
}

function compareActions(
  left: RegistryActionReadMinimal,
  right: RegistryActionReadMinimal,
  field: ActionSortField,
  direction: ActionSortDirection
): number {
  const directionMultiplier = direction === "asc" ? 1 : -1
  const primaryComparison =
    getActionSortValue(left, field).localeCompare(
      getActionSortValue(right, field)
    ) * directionMultiplier

  if (primaryComparison !== 0) {
    return primaryComparison
  }

  const actionComparison =
    left.action.localeCompare(right.action) * directionMultiplier
  if (actionComparison !== 0) {
    return actionComparison
  }

  return (
    getActionDisplayName(left).localeCompare(getActionDisplayName(right)) *
    directionMultiplier
  )
}

function getLastSyncedBadge(lastSyncedAt: string | null | undefined) {
  if (!lastSyncedAt) {
    return (
      <Badge
        key="last-synced"
        variant="secondary"
        className="h-5 px-2 text-[10px] font-normal"
      >
        <Clock3 className="mr-1 size-3" />
        Never synced
      </Badge>
    )
  }

  const syncedAt = new Date(lastSyncedAt)
  const relativeLabel = new Intl.RelativeTimeFormat("en", {
    numeric: "auto",
  })

  const minutes = Math.round((syncedAt.getTime() - Date.now()) / 60000)
  const hours = Math.round(minutes / 60)
  const days = Math.round(hours / 24)

  let value = relativeLabel.format(minutes, "minute")
  if (Math.abs(days) >= 1) {
    value = relativeLabel.format(days, "day")
  } else if (Math.abs(hours) >= 1) {
    value = relativeLabel.format(hours, "hour")
  }

  return (
    <Tooltip>
      <TooltipTrigger asChild>
        <Badge
          key="last-synced"
          variant="secondary"
          className="h-5 cursor-default px-2 text-[10px] font-normal"
        >
          <Clock3 className="mr-1 size-3" />
          {value}
        </Badge>
      </TooltipTrigger>
      <TooltipContent>{format(syncedAt, "PPpp")}</TooltipContent>
    </Tooltip>
  )
}

function RegistryGroupMetadata({ group }: { group: RegistryActionGroup }) {
  const metadataBadges = [
    group.metadata ? getLastSyncedBadge(group.metadata.last_synced_at) : null,
    group.kind === "tracecat" ? (
      <Badge
        key="version"
        variant="secondary"
        className="h-5 px-2 text-[10px] font-normal"
      >
        <HistoryIcon className="mr-1 size-3" />
        {(() => {
          switch (group.currentVersionState?.status) {
            case "ready":
              return `Version ${group.currentVersionState.version}`
            case "loading":
              return "Version Loading..."
            case "truncated":
              return "Version outside recent history"
            case "error":
            case "unavailable":
            default:
              return "Version Unavailable"
          }
        })()}
      </Badge>
    ) : null,
    group.kind === "custom" && group.metadata?.commit_sha ? (
      <Badge
        key="sha"
        variant="secondary"
        className="h-5 px-2 font-mono text-[10px] font-normal"
      >
        <GitCommitHorizontal className="mr-1 size-3" />
        SHA {shortCommitSha(group.metadata.commit_sha)}
      </Badge>
    ) : null,
  ].filter(Boolean)

  if (metadataBadges.length === 0) {
    return null
  }

  return (
    <div className="flex shrink-0 items-center gap-1">{metadataBadges}</div>
  )
}

export function WorkspaceActionsInventory() {
  const { user, userIsLoading, userError } = useAuth()
  const canAdministerOrg = useScopeCheck("org:update")
  const canReadPlatformRegistryMetadata = user?.isPlatformAdmin() ?? false
  const { registryActions, registryActionsIsLoading, registryActionsError } =
    useRegistryActions()
  const { repos, reposIsLoading, reposError } = useRegistryRepositories()
  const { status: platformStatus, isLoading: platformStatusIsLoading } =
    useAdminRegistryStatus({ enabled: canReadPlatformRegistryMetadata })
  const [searchQuery, setSearchQuery] = useState("")
  const [namespaceFilter, setNamespaceFilter] = useState("all")
  const [typeFilter, setTypeFilter] = useState<ActionTypeFilter>("all")
  const [originFilter, setOriginFilter] = useState("all")
  const [sortField, setSortField] = useState<ActionSortField>("action")
  const [sortDirection, setSortDirection] = useState<ActionSortDirection>("asc")
  const [expandedOrigins, setExpandedOrigins] = useState<
    Record<string, boolean>
  >({})

  const platformRepos = platformStatus?.repositories ?? []
  const platformOriginSet = useMemo(
    () => new Set(platformRepos.map((repo) => repo.origin)),
    [platformRepos]
  )
  const customRepo = useMemo(() => getCustomRegistryRepository(repos), [repos])

  const platformVersionsResults = useQueries({
    queries: platformRepos.map((repo) => ({
      queryKey: ["admin", "registry", "versions", repo.id],
      queryFn: async () =>
        await adminRegistryListRegistryVersions({
          repositoryId: repo.id,
          limit: PLATFORM_VERSION_FETCH_LIMIT,
        }),
      enabled: canReadPlatformRegistryMetadata,
    })),
  })

  const platformCurrentVersionStateByOrigin = useMemo(() => {
    const versionsByOrigin = new Map<string, TracecatVersionState>()

    for (const [index, repo] of platformRepos.entries()) {
      if (!repo.current_version_id) {
        versionsByOrigin.set(repo.origin, { status: "unavailable" })
        continue
      }

      const result = platformVersionsResults[index]
      if (result?.isLoading) {
        versionsByOrigin.set(repo.origin, { status: "loading" })
        continue
      }
      if (result?.error) {
        versionsByOrigin.set(repo.origin, { status: "error" })
        continue
      }

      const versions = platformVersionsResults[index]?.data as
        | tracecat__admin__registry__schemas__RegistryVersionRead[]
        | undefined
      const currentVersion =
        versions?.find((version) => version.id === repo.current_version_id) ??
        null

      if (currentVersion) {
        versionsByOrigin.set(repo.origin, {
          status: "ready",
          version: currentVersion.version,
        })
        continue
      }

      if ((versions?.length ?? 0) >= PLATFORM_VERSION_FETCH_LIMIT) {
        versionsByOrigin.set(repo.origin, { status: "truncated" })
        continue
      }

      versionsByOrigin.set(repo.origin, { status: "unavailable" })
    }

    return versionsByOrigin
  }, [platformRepos, platformVersionsResults])

  const repoMetadataByOrigin = useMemo(() => {
    const map = new Map<
      string,
      RegistryRepositoryReadMinimal | RepositoryStatus
    >()

    for (const repo of platformRepos) {
      map.set(repo.origin, repo)
    }

    for (const repo of repos ?? []) {
      map.set(repo.origin, repo)
    }

    return map
  }, [platformRepos, repos])

  const namespaceOptions = useMemo(
    () =>
      Array.from(
        new Set((registryActions ?? []).map((action) => action.namespace))
      )
        .sort((left, right) => left.localeCompare(right))
        .map((value) => ({ value, label: value })),
    [registryActions]
  )

  const originOptions = useMemo(() => {
    const uniqueOrigins = Array.from(
      new Set((registryActions ?? []).map((action) => action.origin))
    ).sort((left, right) => left.localeCompare(right))

    return uniqueOrigins.map((origin) => ({
      value: origin,
      label: getRegistryOriginLabel({
        origin,
        platformOrigins: platformOriginSet,
        customOrigin: customRepo?.origin,
      }),
    }))
  }, [customRepo?.origin, platformOriginSet, registryActions])

  const filteredActions = useMemo(() => {
    const normalizedSearch = searchQuery.trim().toLowerCase()

    return (registryActions ?? []).filter((action) => {
      const matchesSearch =
        normalizedSearch.length === 0 ||
        buildSearchText(action).includes(normalizedSearch)
      const matchesNamespace =
        namespaceFilter === "all" || action.namespace === namespaceFilter
      const matchesType = typeFilter === "all" || action.type === typeFilter
      const matchesOrigin =
        originFilter === "all" || action.origin === originFilter

      return matchesSearch && matchesNamespace && matchesType && matchesOrigin
    })
  }, [namespaceFilter, originFilter, registryActions, searchQuery, typeFilter])

  const groupedActions = useMemo(() => {
    const grouped = new Map<string, RegistryActionReadMinimal[]>()

    for (const action of filteredActions) {
      const existing = grouped.get(action.origin)
      if (existing) {
        existing.push(action)
      } else {
        grouped.set(action.origin, [action])
      }
    }

    const groups: RegistryActionGroup[] = Array.from(grouped.entries()).map(
      ([origin, actions]) => {
        const kind = isTracecatRegistryOrigin(origin, platformOriginSet)
          ? "tracecat"
          : customRepo?.origin === origin
            ? "custom"
            : "other"

        return {
          origin,
          label: getRegistryOriginLabel({
            origin,
            platformOrigins: platformOriginSet,
            customOrigin: customRepo?.origin,
          }),
          kind,
          actions: [...actions].sort((left, right) =>
            compareActions(left, right, sortField, sortDirection)
          ),
          metadata: repoMetadataByOrigin.get(origin) ?? null,
          currentVersionState:
            kind === "tracecat"
              ? (platformCurrentVersionStateByOrigin.get(origin) ??
                (canReadPlatformRegistryMetadata && platformStatusIsLoading
                  ? { status: "loading" }
                  : { status: "unavailable" }))
              : null,
        }
      }
    )

    return groups.sort((left, right) => {
      const getRank = (group: RegistryActionGroup) => {
        switch (group.kind) {
          case "tracecat":
            return 0
          case "custom":
            return 1
          default:
            return 2
        }
      }

      const rankDifference = getRank(left) - getRank(right)
      if (rankDifference !== 0) {
        return rankDifference
      }

      return left.origin.localeCompare(right.origin)
    })
  }, [
    customRepo?.origin,
    filteredActions,
    canReadPlatformRegistryMetadata,
    platformCurrentVersionStateByOrigin,
    platformOriginSet,
    platformStatusIsLoading,
    repoMetadataByOrigin,
    sortDirection,
    sortField,
  ])

  useEffect(() => {
    setExpandedOrigins((current) => {
      const next = { ...current }
      let changed = false

      for (const group of groupedActions) {
        if (next[group.origin] === undefined) {
          next[group.origin] = true
          changed = true
        }
      }

      return changed ? next : current
    })
  }, [groupedActions])

  const selectFilters: CatalogHeaderSelectFilter[] = [
    {
      key: "namespace",
      value: namespaceFilter,
      onValueChange: setNamespaceFilter,
      placeholder: "Namespace",
      allValue: "all",
      options: [{ value: "all", label: "All namespaces" }, ...namespaceOptions],
      widthClassName: "w-[180px]",
    },
    {
      key: "type",
      value: typeFilter,
      onValueChange: (value) => setTypeFilter(value as ActionTypeFilter),
      placeholder: "Type",
      allValue: "all",
      options: [
        { value: "all", label: "All types" },
        {
          value: "template",
          label: actionTypeToLabel.template.label,
        },
        {
          value: "udf",
          label: actionTypeToLabel.udf.label,
        },
      ],
      widthClassName: "w-[190px]",
    },
    {
      key: "origin",
      value: originFilter,
      onValueChange: setOriginFilter,
      placeholder: "Origin",
      allValue: "all",
      options: [{ value: "all", label: "All origins" }, ...originOptions],
      widthClassName: "w-[170px]",
    },
    {
      key: "sort-field",
      value: sortField,
      onValueChange: (value) => setSortField(value as ActionSortField),
      placeholder: "Sort by",
      options: [
        { value: "name", label: "Sort by Name" },
        { value: "action", label: "Sort by Action name" },
        { value: "namespace", label: "Sort by Namespace" },
      ],
      widthClassName: "w-[180px]",
    },
    {
      key: "sort-direction",
      value: sortDirection,
      onValueChange: (value) => setSortDirection(value as ActionSortDirection),
      placeholder: "Order",
      options: [
        { value: "asc", label: "A to Z" },
        { value: "desc", label: "Z to A" },
      ],
      widthClassName: "w-[120px]",
    },
  ]

  if (
    userIsLoading ||
    canAdministerOrg === undefined ||
    registryActionsIsLoading ||
    reposIsLoading
  ) {
    return <CenteredSpinner />
  }

  const primaryError = userError ?? registryActionsError ?? reposError

  if (primaryError) {
    return (
      <AlertNotification
        level="error"
        message={
          primaryError instanceof Error
            ? primaryError.message
            : "Error loading actions."
        }
      />
    )
  }

  return (
    <div className="flex h-full min-h-0 flex-col">
      <CatalogHeader
        searchQuery={searchQuery}
        onSearchChange={setSearchQuery}
        searchPlaceholder="Search actions..."
        selectFilters={selectFilters}
        displayCount={filteredActions.length}
        countLabel="actions"
      />

      <ScrollArea className="flex-1 min-h-0 [&>[data-radix-scroll-area-viewport]]:[scrollbar-width:none] [&>[data-radix-scroll-area-viewport]::-webkit-scrollbar]:hidden [&>[data-orientation=vertical]]:!hidden [&>[data-orientation=horizontal]]:!hidden">
        <div className="w-full pb-10">
          {groupedActions.map((group) => {
            const isExpanded = expandedOrigins[group.origin] ?? true
            const primaryLabel =
              group.kind === "tracecat" ? group.origin : group.label
            const showSecondaryOrigin = primaryLabel !== group.origin

            return (
              <Collapsible
                key={group.origin}
                open={isExpanded}
                onOpenChange={(nextOpen) =>
                  setExpandedOrigins((current) => ({
                    ...current,
                    [group.origin]: nextOpen,
                  }))
                }
              >
                <div className="border-b border-border/50">
                  <CollapsibleTrigger asChild>
                    <button
                      type="button"
                      className="flex w-full items-center gap-2 px-3 py-1 text-left transition-colors hover:bg-muted/50 [&[data-state=open]_.chevron]:rotate-90"
                    >
                      <div className="flex h-6 w-6 shrink-0 items-center justify-center">
                        <ChevronRight className="chevron size-4 text-muted-foreground transition-transform duration-200" />
                      </div>
                      <div className="min-w-0 flex-1">
                        <div className="flex min-w-0 items-center gap-2">
                          <span className="truncate text-xs font-medium">
                            {primaryLabel}
                          </span>
                          <span className="text-xs text-muted-foreground">
                            {group.actions.length}
                          </span>
                        </div>
                        {showSecondaryOrigin ? (
                          <div className="truncate text-xs text-muted-foreground">
                            {group.origin}
                          </div>
                        ) : null}
                      </div>
                      <RegistryGroupMetadata group={group} />
                    </button>
                  </CollapsibleTrigger>

                  <CollapsibleContent>
                    <div className="divide-y divide-border/50">
                      {group.actions.map((action) => {
                        const typeLabel = actionTypeToLabel[action.type].label
                        const actionTitle = getActionDisplayName(action)

                        return (
                          <Item
                            key={action.id}
                            variant="default"
                            size="sm"
                            className="w-full flex-nowrap rounded-none border-none px-3 py-1.5 text-left"
                          >
                            <ItemMedia className="translate-y-0 self-center">
                              {getIcon(action.action, {
                                className: "size-6 rounded border",
                              })}
                            </ItemMedia>
                            <ItemContent className="min-w-0 gap-0">
                              <ItemTitle className="flex w-full min-w-0 items-center gap-2 text-xs">
                                <span className="truncate">{actionTitle}</span>
                              </ItemTitle>
                              <ItemDescription className="truncate font-mono text-[11px]">
                                {action.action}
                              </ItemDescription>
                            </ItemContent>
                            <ItemActions className="ml-auto flex shrink-0 items-center gap-1.5 pl-3">
                              <Badge
                                variant="secondary"
                                className="h-5 px-2 text-[10px] font-normal"
                              >
                                {typeLabel}
                              </Badge>
                              <Badge
                                variant="secondary"
                                className="h-5 px-2 text-[10px] font-normal"
                              >
                                {action.namespace}
                              </Badge>
                              <DropdownMenu>
                                <DropdownMenuTrigger asChild>
                                  <Button
                                    variant="ghost"
                                    className="size-8 p-0"
                                  >
                                    <span className="sr-only">Open menu</span>
                                    <DotsHorizontalIcon className="size-4" />
                                  </Button>
                                </DropdownMenuTrigger>
                                <DropdownMenuContent align="end">
                                  <DropdownMenuItem
                                    onSelect={async () => {
                                      try {
                                        await copyToClipboard({
                                          value: action.action,
                                        })
                                        toast({
                                          title: "Action name copied",
                                          description: action.action,
                                        })
                                      } catch (error) {
                                        console.error(error)
                                        toast({
                                          title: "Failed to copy action name",
                                          description:
                                            "Please try again or copy manually",
                                          variant: "destructive",
                                        })
                                      }
                                    }}
                                  >
                                    Copy action name
                                  </DropdownMenuItem>
                                </DropdownMenuContent>
                              </DropdownMenu>
                            </ItemActions>
                          </Item>
                        )
                      })}
                    </div>
                  </CollapsibleContent>
                </div>
              </Collapsible>
            )
          })}

          {groupedActions.length === 0 ? (
            <div className="py-12 text-center text-sm text-muted-foreground">
              No actions found matching your criteria.
            </div>
          ) : null}
        </div>
      </ScrollArea>
    </div>
  )
}
