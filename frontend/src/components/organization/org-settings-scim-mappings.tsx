"use client"

import { AlertTriangleIcon, SearchIcon, UsersIcon, XIcon } from "lucide-react"
import { type ReactNode, useState } from "react"
import type {
  ExternalGroupMappingRead,
  ExternalGroupRead,
  ScimConnectionStatus,
} from "@/client"
import { useScopeCheck } from "@/components/auth/scope-guard"
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
import { Badge } from "@/components/ui/badge"
import { Button } from "@/components/ui/button"
import {
  Empty,
  EmptyDescription,
  EmptyHeader,
  EmptyMedia,
  EmptyTitle,
} from "@/components/ui/empty"
import { Input } from "@/components/ui/input"
import {
  Select,
  SelectContent,
  SelectItem,
  SelectTrigger,
  SelectValue,
} from "@/components/ui/select"
import {
  Table,
  TableBody,
  TableCell,
  TableHead,
  TableHeader,
  TableRow,
} from "@/components/ui/table"
import { useScimExternalGroups, useScimMappings } from "@/hooks/use-scim"
import { useRbacGroups } from "@/lib/hooks"
import { cn } from "@/lib/utils"

/** A mapping the admin has proposed but not applied. */
export type ScimMappingDraft = Pick<
  ExternalGroupMappingRead,
  | "external_group_id"
  | "external_group_display_name"
  | "group_id"
  | "group_name"
>

type Target = ScimMappingDraft & { mapping?: ExternalGroupMappingRead }

function ErrorAlert({ title, children }: { title: string; children?: string }) {
  return (
    <Alert>
      <AlertTriangleIcon className="size-4 !text-destructive" />
      <AlertTitle className="text-destructive">{title}</AlertTitle>
      {children ? <AlertDescription>{children}</AlertDescription> : null}
    </Alert>
  )
}

/**
 * Map synced IdP groups onto Tracecat groups, one row per IdP group.
 *
 * While the connection is pending, additions are local drafts applied on
 * activation. Once active, each addition is reviewed before it is created.
 */
export function OrgSettingsScimMappings({
  connected,
  status,
  revoked,
  drafts,
  reviewIsPending,
  onAdd,
  onRemoveDraft,
  onDiscardDrafts,
}: {
  connected: boolean
  status?: ScimConnectionStatus
  revoked: boolean
  drafts: ScimMappingDraft[]
  reviewIsPending: boolean
  onAdd: (draft: ScimMappingDraft) => void
  onRemoveDraft: (draft: ScimMappingDraft) => void
  onDiscardDrafts: () => void
}) {
  const canManage = useScopeCheck("org:scim:manage") === true
  const isPending = status === "pending"
  const canEdit = canManage && !revoked && (isPending || status === "active")

  const [query, setQuery] = useState("")
  const [unmappedOnly, setUnmappedOnly] = useState(false)
  const [removing, setRemoving] = useState<ExternalGroupMappingRead | null>(
    null
  )

  const {
    externalGroups,
    externalGroupsIsLoading,
    externalGroupsError,
    externalGroupsHasNextPage,
    externalGroupsIsFetchingNextPage,
    fetchNextExternalGroups,
  } = useScimExternalGroups()
  const {
    mappings,
    mappingsIsLoading,
    mappingsError,
    mappingsHasNextPage,
    mappingsIsFetchingNextPage,
    fetchNextMappings,
    deleteMapping,
    deleteMappingIsPending,
  } = useScimMappings()
  const {
    groups,
    isLoading: groupsIsLoading,
    error: groupsError,
  } = useRbacGroups()

  const header = (
    <div className="space-y-1">
      <h3 className="text-lg font-medium">Group mappings</h3>
      <p className="text-sm text-muted-foreground">
        Members of a mapped IdP group get that Tracecat group&apos;s access.
        Unmapped groups grant nothing.
      </p>
    </div>
  )

  if (!connected) {
    return (
      <div className="space-y-4">
        {header}
        <Empty className="gap-4 rounded-lg border py-12">
          <EmptyHeader>
            <EmptyMedia variant="icon">
              <UsersIcon className="size-6" />
            </EmptyMedia>
            <EmptyTitle>Connect SCIM first</EmptyTitle>
            <EmptyDescription>
              Generate a connection token above, then configure Tracecat in your
              identity provider. Groups you push appear here and can be mapped.
            </EmptyDescription>
          </EmptyHeader>
        </Empty>
      </div>
    )
  }

  if (externalGroupsIsLoading || mappingsIsLoading || groupsIsLoading) {
    return (
      <div className="space-y-4">
        {header}
        <CenteredSpinner />
      </div>
    )
  }

  if (
    (externalGroupsError && !externalGroups?.length) ||
    (mappingsError && !mappings?.length) ||
    groupsError
  ) {
    return (
      <div className="space-y-4">
        {header}
        <ErrorAlert title="Unable to load group mappings">
          Reload the page to try again.
        </ErrorAlert>
      </div>
    )
  }

  const allGroups = externalGroups ?? []
  const tracecatGroups = groups ?? []

  function targetsFor(group: ExternalGroupRead): Target[] {
    if (isPending) {
      return drafts.filter((draft) => draft.external_group_id === group.id)
    }
    return (mappings ?? [])
      .filter((mapping) => mapping.external_group_id === group.id)
      .map((mapping) => ({ ...mapping, mapping }))
  }

  const rows = allGroups.map((group) => ({ group, targets: targetsFor(group) }))
  const unmappedCount = rows.filter((row) => row.targets.length === 0).length
  const needle = query.trim().toLowerCase()
  const visibleRows = rows.filter(
    (row) =>
      (!unmappedOnly || row.targets.length === 0) &&
      (!needle || row.group.display_name.toLowerCase().includes(needle))
  )

  return (
    <div className="space-y-4">
      {header}
      {revoked && (
        <ErrorAlert title="Token revoked">
          Rotate the token before changing SCIM configuration.
        </ErrorAlert>
      )}

      {allGroups.length === 0 ? (
        <Empty className="gap-4 rounded-lg border py-12">
          <EmptyHeader>
            <EmptyMedia variant="icon">
              <UsersIcon className="size-6" />
            </EmptyMedia>
            <EmptyTitle>No synced groups yet</EmptyTitle>
            <EmptyDescription>
              Assign groups to the Tracecat application in your identity
              provider. They appear here once it pushes them.
            </EmptyDescription>
          </EmptyHeader>
        </Empty>
      ) : (
        <div className="overflow-hidden rounded-lg border">
          <div className="flex items-center gap-3 border-b px-4 py-3">
            <div className="relative flex-1">
              <SearchIcon className="pointer-events-none absolute left-3 top-1/2 size-4 -translate-y-1/2 text-muted-foreground" />
              <Input
                type="search"
                value={query}
                onChange={(event) => setQuery(event.target.value)}
                placeholder="Search identity provider groups"
                aria-label="Search identity provider groups"
                className="pl-9"
              />
            </div>
            <div className="flex gap-0.5 rounded-md border p-0.5">
              <FilterButton
                active={!unmappedOnly}
                onClick={() => setUnmappedOnly(false)}
              >
                All {rows.length}
              </FilterButton>
              <FilterButton
                active={unmappedOnly}
                onClick={() => setUnmappedOnly(true)}
              >
                Unmapped {unmappedCount}
              </FilterButton>
            </div>
          </div>

          <Table>
            <TableHeader>
              <TableRow className="hover:bg-transparent">
                <TableHead>Identity provider group</TableHead>
                <TableHead className="w-24">Members</TableHead>
                <TableHead className="w-[340px]">Tracecat groups</TableHead>
                <TableHead className="w-28">Status</TableHead>
              </TableRow>
            </TableHeader>
            <TableBody>
              {visibleRows.map(({ group, targets }) => (
                <TableRow key={group.id} className="hover:bg-transparent">
                  <TableCell className="font-medium">
                    {group.display_name}
                  </TableCell>
                  <TableCell className="text-muted-foreground">
                    {group.member_count}
                  </TableCell>
                  <TableCell>
                    <div className="flex flex-wrap items-center gap-1.5">
                      {targets.map((target) => (
                        <Badge
                          key={target.group_id}
                          variant="secondary"
                          className="gap-1 pr-1 font-normal"
                        >
                          {target.group_name}
                          <button
                            type="button"
                            className="rounded-sm p-0.5 text-muted-foreground hover:text-foreground disabled:opacity-50"
                            disabled={
                              !canEdit ||
                              reviewIsPending ||
                              deleteMappingIsPending
                            }
                            aria-label={`Remove ${target.group_name} from ${group.display_name}`}
                            onClick={() => {
                              if (target.mapping) setRemoving(target.mapping)
                              else onRemoveDraft(target)
                            }}
                          >
                            <XIcon className="size-3" />
                          </button>
                        </Badge>
                      ))}
                      <AddTargetSelect
                        group={group}
                        tracecatGroups={tracecatGroups.filter(
                          (candidate) =>
                            !targets.some(
                              (target) => target.group_id === candidate.id
                            )
                        )}
                        hasTargets={targets.length > 0}
                        disabled={!canEdit || reviewIsPending}
                        onAdd={onAdd}
                      />
                    </div>
                  </TableCell>
                  <TableCell>
                    {targets.length === 0 ? (
                      <span className="text-sm text-muted-foreground">
                        No access
                      </span>
                    ) : (
                      <Badge variant="secondary" className="font-normal">
                        {isPending ? "Draft" : "Mapped"}
                      </Badge>
                    )}
                  </TableCell>
                </TableRow>
              ))}
              {visibleRows.length === 0 && (
                <TableRow className="hover:bg-transparent">
                  <TableCell
                    colSpan={4}
                    className="py-6 text-center text-sm text-muted-foreground"
                  >
                    No groups match.
                  </TableCell>
                </TableRow>
              )}
            </TableBody>
          </Table>

          <ListFooter
            isPending={isPending}
            drafts={drafts}
            onDiscardDrafts={onDiscardDrafts}
            externalGroupsError={Boolean(externalGroupsError)}
            externalGroupsHasNextPage={Boolean(externalGroupsHasNextPage)}
            externalGroupsIsFetchingNextPage={externalGroupsIsFetchingNextPage}
            fetchNextExternalGroups={() => void fetchNextExternalGroups()}
            mappingsError={Boolean(mappingsError)}
            mappingsHasNextPage={Boolean(mappingsHasNextPage)}
            mappingsIsFetchingNextPage={mappingsIsFetchingNextPage}
            fetchNextMappings={() => void fetchNextMappings()}
          />
        </div>
      )}

      <AlertDialog
        open={Boolean(removing)}
        onOpenChange={(open) => {
          if (!open && !deleteMappingIsPending) setRemoving(null)
        }}
      >
        <AlertDialogContent>
          <AlertDialogHeader>
            <AlertDialogTitle>Remove mapping</AlertDialogTitle>
            <AlertDialogDescription>
              {removing
                ? `${removing.external_group_display_name} stops granting ${removing.group_name}. `
                : ""}
              If this is the final mapping, current members are retained as
              manual members. Other grants are unchanged.
            </AlertDialogDescription>
          </AlertDialogHeader>
          <AlertDialogFooter>
            <AlertDialogCancel disabled={deleteMappingIsPending}>
              Cancel
            </AlertDialogCancel>
            <Button
              variant="destructive"
              disabled={deleteMappingIsPending}
              onClick={() => {
                if (!removing) return
                void deleteMapping(removing.id)
                  .then(() => setRemoving(null))
                  .catch(() => {})
              }}
            >
              Remove mapping
            </Button>
          </AlertDialogFooter>
        </AlertDialogContent>
      </AlertDialog>
    </div>
  )
}

function FilterButton({
  active,
  onClick,
  children,
}: {
  active: boolean
  onClick: () => void
  children: ReactNode
}) {
  return (
    <button
      type="button"
      aria-pressed={active}
      onClick={onClick}
      className={cn(
        "h-7 rounded-sm px-3 text-xs font-medium transition-colors",
        active
          ? "bg-muted text-foreground"
          : "text-muted-foreground hover:text-foreground"
      )}
    >
      {children}
    </button>
  )
}

function AddTargetSelect({
  group,
  tracecatGroups,
  hasTargets,
  disabled,
  onAdd,
}: {
  group: ExternalGroupRead
  tracecatGroups: { id: string; name: string }[]
  hasTargets: boolean
  disabled: boolean
  onAdd: (draft: ScimMappingDraft) => void
}) {
  if (hasTargets && tracecatGroups.length === 0) {
    return null
  }
  return (
    <Select
      value=""
      disabled={disabled}
      onValueChange={(groupId) => {
        const target = tracecatGroups.find((item) => item.id === groupId)
        if (!target) return
        onAdd({
          external_group_id: group.id,
          external_group_display_name: group.display_name,
          group_id: target.id,
          group_name: target.name,
        })
      }}
    >
      <SelectTrigger
        aria-label={`Add Tracecat group to ${group.display_name}`}
        className={cn(
          "h-7 text-xs",
          hasTargets ? "w-auto gap-1 border-dashed" : "w-full"
        )}
      >
        <SelectValue placeholder={hasTargets ? "Add" : "Select a group"} />
      </SelectTrigger>
      <SelectContent>
        {tracecatGroups.map((target) => (
          <SelectItem key={target.id} value={target.id}>
            {target.name}
          </SelectItem>
        ))}
      </SelectContent>
    </Select>
  )
}

function ListFooter({
  isPending,
  drafts,
  onDiscardDrafts,
  externalGroupsError,
  externalGroupsHasNextPage,
  externalGroupsIsFetchingNextPage,
  fetchNextExternalGroups,
  mappingsError,
  mappingsHasNextPage,
  mappingsIsFetchingNextPage,
  fetchNextMappings,
}: {
  isPending: boolean
  drafts: ScimMappingDraft[]
  onDiscardDrafts: () => void
  externalGroupsError: boolean
  externalGroupsHasNextPage: boolean
  externalGroupsIsFetchingNextPage: boolean
  fetchNextExternalGroups: () => void
  mappingsError: boolean
  mappingsHasNextPage: boolean
  mappingsIsFetchingNextPage: boolean
  fetchNextMappings: () => void
}) {
  const showDrafts = isPending && drafts.length > 0
  const showMappingPages = !isPending && (mappingsHasNextPage || mappingsError)
  if (
    !showDrafts &&
    !showMappingPages &&
    !externalGroupsHasNextPage &&
    !externalGroupsError
  ) {
    return null
  }
  return (
    <div className="flex flex-wrap items-center gap-3 border-t bg-muted/30 px-4 py-2.5 text-sm">
      {showDrafts ? (
        <span className="flex-1 text-muted-foreground">
          <span className="font-medium text-foreground">
            {drafts.length} draft {drafts.length === 1 ? "mapping" : "mappings"}
          </span>{" "}
          · applied only when you activate
        </span>
      ) : (
        <span className="flex-1" />
      )}
      {externalGroupsError && (
        <span role="alert" className="text-muted-foreground">
          Unable to load more groups.
        </span>
      )}
      {(externalGroupsHasNextPage || externalGroupsError) && (
        <Button
          variant="outline"
          size="sm"
          disabled={externalGroupsIsFetchingNextPage}
          onClick={fetchNextExternalGroups}
        >
          {externalGroupsIsFetchingNextPage
            ? "Loading groups…"
            : "Load more groups"}
        </Button>
      )}
      {!isPending && mappingsError && (
        <span role="alert" className="text-muted-foreground">
          Unable to load more mappings.
        </span>
      )}
      {showMappingPages && (
        <Button
          variant="outline"
          size="sm"
          disabled={mappingsIsFetchingNextPage}
          onClick={fetchNextMappings}
        >
          {mappingsIsFetchingNextPage
            ? "Loading mappings…"
            : "Load more mappings"}
        </Button>
      )}
      {showDrafts && (
        <Button variant="ghost" size="sm" onClick={onDiscardDrafts}>
          Discard drafts
        </Button>
      )}
    </div>
  )
}
