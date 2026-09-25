"use client"

import {
  AlertTriangleIcon,
  ArrowRightIcon,
  ChevronDownIcon,
  SearchIcon,
  UsersIcon,
} from "lucide-react"
import { type ReactNode, useState } from "react"
import type {
  ExternalGroupMappingRead,
  ExternalGroupRead,
  ScimConnectionStatus,
} from "@/client"
import { useScopeCheck } from "@/components/auth/scope-guard"
import { CenteredSpinner } from "@/components/loading/spinner"
import { Alert, AlertDescription, AlertTitle } from "@/components/ui/alert"
import { Button } from "@/components/ui/button"
import { CheckIndicator } from "@/components/ui/check-indicator"
import {
  Command,
  CommandEmpty,
  CommandGroup,
  CommandInput,
  CommandItem,
  CommandList,
} from "@/components/ui/command"
import {
  Empty,
  EmptyDescription,
  EmptyHeader,
  EmptyMedia,
  EmptyTitle,
} from "@/components/ui/empty"
import { Input } from "@/components/ui/input"
import {
  Popover,
  PopoverContent,
  PopoverTrigger,
} from "@/components/ui/popover"
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
 * Every change is a local draft. Pending connections apply drafts on
 * activation; active ones apply them together after one review.
 */
export function OrgSettingsScimMappings({
  connected,
  status,
  revoked,
  drafts,
  removals,
  reviewIsPending,
  onAdd,
  onRemove,
  onDiscardDrafts,
  onReviewChanges,
}: {
  connected: boolean
  status?: ScimConnectionStatus
  revoked: boolean
  drafts: ScimMappingDraft[]
  removals: ExternalGroupMappingRead[]
  reviewIsPending: boolean
  onAdd: (draft: ScimMappingDraft) => void
  onRemove: (
    draft: ScimMappingDraft,
    mapping?: ExternalGroupMappingRead
  ) => void
  onDiscardDrafts: () => void
  onReviewChanges: () => void
}) {
  const canManage = useScopeCheck("org:scim:manage") === true
  const isPending = status === "pending"
  const canEdit = canManage && !revoked && (isPending || status === "active")

  const [query, setQuery] = useState("")

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
  } = useScimMappings()
  const {
    groups,
    isLoading: groupsIsLoading,
    error: groupsError,
  } = useRbacGroups()

  const header = (
    <div className="flex items-end gap-4">
      <div className="flex-1 space-y-1">
        <h3 className="text-lg font-medium">Group mappings</h3>
        <p className="text-sm text-muted-foreground">
          Unmapped groups grant no access.
        </p>
      </div>
      {externalGroups && externalGroups.length > 0 ? (
        <div className="relative w-60">
          <SearchIcon className="pointer-events-none absolute left-2.5 top-1/2 size-3.5 -translate-y-1/2 text-muted-foreground" />
          <Input
            type="search"
            value={query}
            onChange={(event) => setQuery(event.target.value)}
            placeholder="Search groups"
            aria-label="Search groups"
            className="h-8 pl-8 text-sm"
          />
        </div>
      ) : null}
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
    const removed = new Set(removals.map((mapping) => mapping.id))
    const live: Target[] = (mappings ?? [])
      .filter(
        (mapping) =>
          mapping.external_group_id === group.id && !removed.has(mapping.id)
      )
      .map((mapping) => ({ ...mapping, mapping }))
    return [
      ...live,
      ...drafts.filter((draft) => draft.external_group_id === group.id),
    ]
  }

  const rows = allGroups.map((group) => ({ group, targets: targetsFor(group) }))
  const needle = query.trim().toLowerCase()
  const visibleRows = rows.filter(
    (row) => !needle || row.group.display_name.toLowerCase().includes(needle)
  )

  return (
    <div className="space-y-4">
      {header}
      {status === "disabled" && (
        <ErrorAlert title="SCIM disconnected">
          Generate a new token to reconnect.
        </ErrorAlert>
      )}
      {revoked && status !== "disabled" && (
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
          <Table>
            <TableHeader>
              <TableRow className="hover:bg-transparent">
                <TableHead>IdP group</TableHead>
                <TableHead className="w-24">Members</TableHead>
                <TableHead className="w-8" />
                <TableHead className="w-[320px]">Tracecat groups</TableHead>
              </TableRow>
            </TableHeader>
            <TableBody>
              {visibleRows.map(({ group, targets }) => (
                <TableRow key={group.id} className="hover:bg-transparent">
                  <TableCell className="align-top font-medium">
                    <RowLine>{group.display_name}</RowLine>
                  </TableCell>
                  <TableCell className="align-top text-muted-foreground">
                    <RowLine>{group.member_count}</RowLine>
                  </TableCell>
                  <TableCell className="align-top px-0">
                    <RowLine className="justify-center">
                      <ArrowRightIcon className="size-4 text-muted-foreground/60" />
                    </RowLine>
                  </TableCell>
                  <TableCell className="align-top">
                    <TargetPicker
                      group={group}
                      targets={targets}
                      tracecatGroups={tracecatGroups}
                      disabled={!canEdit || reviewIsPending}
                      onAdd={onAdd}
                      onRemove={(target) => {
                        onRemove(target, target.mapping)
                      }}
                    />
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
            changeCount={drafts.length + removals.length}
            reviewIsPending={reviewIsPending}
            onDiscardDrafts={onDiscardDrafts}
            onReviewChanges={onReviewChanges}
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
    </div>
  )
}

/** Centers a cell's content on the picker's first line. */
function RowLine({
  className,
  children,
}: {
  className?: string
  children: ReactNode
}) {
  return (
    <div className={cn("flex h-9 items-center", className)}>{children}</div>
  )
}

/** One picker per IdP group: shows its Tracecat groups, toggles each one. */
function TargetPicker({
  group,
  targets,
  tracecatGroups,
  disabled,
  onAdd,
  onRemove,
}: {
  group: ExternalGroupRead
  targets: Target[]
  tracecatGroups: { id: string; name: string }[]
  disabled: boolean
  onAdd: (draft: ScimMappingDraft) => void
  onRemove: (target: Target) => void
}) {
  const [open, setOpen] = useState(false)
  const names = targets
    .map((target) => target.group_name)
    .sort((a, b) => a.localeCompare(b))
  return (
    <Popover open={open} onOpenChange={setOpen}>
      <PopoverTrigger asChild>
        <Button
          variant="outline"
          disabled={disabled}
          aria-label={`Tracecat groups for ${group.display_name}`}
          className={cn(
            "h-auto min-h-9 w-full items-start justify-between whitespace-normal px-3 py-[7px] font-normal shadow-none",
            names.length === 0 &&
              "border-dashed bg-muted/30 text-muted-foreground"
          )}
        >
          <span className="flex min-w-0 flex-col text-left">
            {names.length === 0 ? (
              <span>Not mapped</span>
            ) : (
              names.map((name) => (
                <span key={name} className="truncate">
                  {name}
                </span>
              ))
            )}
          </span>
          <span className="flex h-5 items-center">
            <ChevronDownIcon className="size-4 shrink-0 text-muted-foreground" />
          </span>
        </Button>
      </PopoverTrigger>
      <PopoverContent
        align="start"
        className="w-[var(--radix-popover-trigger-width)] p-0"
      >
        <Command>
          <CommandInput
            placeholder="Search Tracecat groups"
            className="text-sm"
          />
          <CommandList>
            <CommandEmpty>No groups found.</CommandEmpty>
            <CommandGroup>
              {tracecatGroups.map((candidate) => {
                const target = targets.find(
                  (item) => item.group_id === candidate.id
                )
                return (
                  <CommandItem
                    key={candidate.id}
                    value={candidate.id}
                    keywords={[candidate.name]}
                    onSelect={() => {
                      if (target) {
                        onRemove(target)
                        return
                      }
                      onAdd({
                        external_group_id: group.id,
                        external_group_display_name: group.display_name,
                        group_id: candidate.id,
                        group_name: candidate.name,
                      })
                    }}
                  >
                    <CheckIndicator checked={Boolean(target)} />
                    <span className="truncate">{candidate.name}</span>
                  </CommandItem>
                )
              })}
            </CommandGroup>
          </CommandList>
        </Command>
      </PopoverContent>
    </Popover>
  )
}

function ListFooter({
  isPending,
  changeCount,
  reviewIsPending,
  onDiscardDrafts,
  onReviewChanges,
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
  changeCount: number
  reviewIsPending: boolean
  onDiscardDrafts: () => void
  onReviewChanges: () => void
  externalGroupsError: boolean
  externalGroupsHasNextPage: boolean
  externalGroupsIsFetchingNextPage: boolean
  fetchNextExternalGroups: () => void
  mappingsError: boolean
  mappingsHasNextPage: boolean
  mappingsIsFetchingNextPage: boolean
  fetchNextMappings: () => void
}) {
  const showDrafts = changeCount > 0
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
          {changeCount} {changeCount === 1 ? "draft applies" : "drafts apply"}{" "}
          {isPending ? "when you activate." : "after review."}
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
      {showDrafts && !isPending && (
        <Button size="sm" disabled={reviewIsPending} onClick={onReviewChanges}>
          Review changes
        </Button>
      )}
    </div>
  )
}
