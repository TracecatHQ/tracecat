"use client"

import {
  type ColumnDef,
  getCoreRowModel,
  getPaginationRowModel,
  useReactTable,
} from "@tanstack/react-table"
import {
  ArrowUpIcon,
  DiffIcon,
  MoreHorizontalIcon,
  RefreshCcw,
  TagIcon,
  Trash2Icon,
} from "lucide-react"
import { Fragment, useMemo } from "react"
import type {
  GitCommitInfo,
  tracecat__registry__repositories__schemas__RegistryVersionRead,
} from "@/client"
import { DataTablePagination } from "@/components/data-table/pagination"
import { Spinner } from "@/components/loading/spinner"
import { shortCommitSha, shortVersion } from "@/components/registry/utils"
import { Badge } from "@/components/ui/badge"
import { Button } from "@/components/ui/button"
import {
  DropdownMenu,
  DropdownMenuContent,
  DropdownMenuItem,
  DropdownMenuSeparator,
  DropdownMenuTrigger,
} from "@/components/ui/dropdown-menu"
import {
  Table,
  TableBody,
  TableCell,
  TableHead,
  TableHeader,
  TableRow,
} from "@/components/ui/table"
import { getRelativeTime } from "@/lib/event-history"

type RegistryVersionRead =
  tracecat__registry__repositories__schemas__RegistryVersionRead

/** One table row: a remote commit, a synced version, or both joined on SHA. */
export type VersionRow = {
  key: string
  commit: GitCommitInfo | null
  version: RegistryVersionRead | null
  isHead: boolean
  isCurrent: boolean
  /** First row of a commit shows the message; extra version rows repeat only the SHA. */
  showCommitDetails: boolean
}

/** Props for {@link OrgRegistryVersionsTable}. */
export interface OrgRegistryVersionsTableProps {
  commitRows: VersionRow[]
  otherRows: VersionRow[]
  canUpdate: boolean
  canDelete: boolean
  canCompare: boolean
  pendingKey: string | null
  mutationInFlight: boolean
  onSyncCommit: (commit: GitCommitInfo) => void
  onPromote: (version: RegistryVersionRead) => void
  onCompare: (version: RegistryVersionRead) => void
  onDelete: (version: RegistryVersionRead) => void
}

const COLUMN_COUNT = 5
const DEFAULT_PAGE_SIZE = 10

/**
 * Rendering is custom, so the table only needs row identity for pagination.
 * Columns are declared for TanStack's bookkeeping, never rendered.
 */
const PAGINATION_COLUMNS: ColumnDef<VersionRow>[] = []

/** Commit-driven table of registry versions with per-row actions. */
export function OrgRegistryVersionsTable({
  commitRows,
  otherRows,
  canUpdate,
  canDelete,
  canCompare,
  pendingKey,
  mutationInFlight,
  onSyncCommit,
  onPromote,
  onCompare,
  onDelete,
}: OrgRegistryVersionsTableProps) {
  const rowProps = {
    canUpdate,
    canDelete,
    canCompare,
    pendingKey,
    mutationInFlight,
    onSyncCommit,
    onPromote,
    onCompare,
    onDelete,
  }

  const rows = useMemo(
    () => [...commitRows, ...otherRows],
    [commitRows, otherRows]
  )
  // autoResetPageIndex (default) returns to page 1 whenever `rows` changes
  // identity, e.g. after a sync or delete refetch.
  const table = useReactTable({
    data: rows,
    columns: PAGINATION_COLUMNS,
    getRowId: (row) => row.key,
    getCoreRowModel: getCoreRowModel(),
    getPaginationRowModel: getPaginationRowModel(),
    initialState: { pagination: { pageSize: DEFAULT_PAGE_SIZE } },
  })

  const pageRows = table.getRowModel().rows
  const firstOtherIndex = pageRows.findIndex(
    (row) => row.original.commit === null
  )

  return (
    <div className="space-y-4">
      <Table>
        <TableHeader>
          <TableRow>
            <TableHead className="w-[200px]">Commit</TableHead>
            <TableHead>Message</TableHead>
            <TableHead className="w-[110px]">Status</TableHead>
            <TableHead className="w-[130px]">Synced</TableHead>
            <TableHead className="w-10">
              <span className="sr-only">Actions</span>
            </TableHead>
          </TableRow>
        </TableHeader>
        <TableBody>
          {pageRows.map((row, index) => (
            <Fragment key={row.id}>
              {index === firstOtherIndex && (
                <TableRow className="hover:bg-transparent">
                  <TableCell
                    colSpan={COLUMN_COUNT}
                    className="bg-muted/40 py-1.5 text-xs text-muted-foreground"
                  >
                    Other synced versions
                  </TableCell>
                </TableRow>
              )}
              <VersionTableRow row={row.original} {...rowProps} />
            </Fragment>
          ))}
        </TableBody>
      </Table>
      <DataTablePagination table={table} />
    </div>
  )
}

interface VersionTableRowProps
  extends Omit<OrgRegistryVersionsTableProps, "commitRows" | "otherRows"> {
  row: VersionRow
}

function VersionTableRow({
  row,
  canUpdate,
  canDelete,
  canCompare,
  pendingKey,
  mutationInFlight,
  onSyncCommit,
  onPromote,
  onCompare,
  onDelete,
}: VersionTableRowProps) {
  const { commit, version } = row
  const isPending = pendingKey === row.key
  const showSync = commit !== null && version === null && canUpdate
  const showPromote = version !== null && !row.isCurrent && canUpdate
  const showCompare = version !== null && canCompare
  const showDelete = version !== null && !row.isCurrent && canDelete
  const hasMenu = showSync || showPromote || showCompare || showDelete
  const suffix = version ? getVersionSuffix(version) : null
  const sha = commit?.sha ?? version?.commit_sha
  const label = sha ? shortCommitSha(sha) : shortVersion(version?.version ?? "")

  return (
    <TableRow>
      <TableCell className="align-top">
        <div className="flex flex-wrap items-center gap-1.5">
          <span className="font-mono text-xs">{label}</span>
          {row.isHead && row.showCommitDetails && (
            <Badge variant="secondary" className="text-xs">
              HEAD
            </Badge>
          )}
          {row.showCommitDetails &&
            commit?.tags?.map((tag) => (
              <Badge
                key={tag}
                variant="outline"
                className="flex items-center gap-1 text-xs font-normal text-muted-foreground"
              >
                <TagIcon className="size-2.5" />
                {tag}
              </Badge>
            ))}
          {suffix && (
            <span className="font-mono text-xs text-muted-foreground">
              {suffix}
            </span>
          )}
        </div>
      </TableCell>
      <TableCell className="align-top">
        {commit && row.showCommitDetails ? (
          <div className="space-y-0.5">
            <p className="line-clamp-1 text-sm">
              {commit.message.split("\n")[0]}
            </p>
            <p className="text-xs text-muted-foreground">
              {commit.author || "Unknown"} ·{" "}
              {getRelativeTime(new Date(commit.date))}
            </p>
          </div>
        ) : (
          <span className="text-muted-foreground">—</span>
        )}
      </TableCell>
      <TableCell className="align-top">
        <VersionStatus row={row} />
      </TableCell>
      <TableCell className="align-top text-sm text-muted-foreground">
        {version ? getRelativeTime(new Date(version.created_at)) : "—"}
      </TableCell>
      <TableCell className="text-right align-top">
        {hasMenu && (
          <DropdownMenu>
            <DropdownMenuTrigger asChild>
              <Button
                variant="ghost"
                size="icon"
                className="size-7"
                aria-label="Version actions"
              >
                {isPending ? (
                  <Spinner className="size-3.5" />
                ) : (
                  <MoreHorizontalIcon className="size-4" />
                )}
              </Button>
            </DropdownMenuTrigger>
            <DropdownMenuContent align="end">
              {showSync && commit && (
                <DropdownMenuItem
                  disabled={mutationInFlight}
                  onSelect={() => onSyncCommit(commit)}
                >
                  <RefreshCcw className="mr-2 size-4" />
                  Sync this commit
                </DropdownMenuItem>
              )}
              {showPromote && version && (
                <DropdownMenuItem
                  disabled={mutationInFlight}
                  onSelect={() => onPromote(version)}
                >
                  <ArrowUpIcon className="mr-2 size-4" />
                  Promote
                </DropdownMenuItem>
              )}
              {showCompare && version && (
                <DropdownMenuItem onSelect={() => onCompare(version)}>
                  <DiffIcon className="mr-2 size-4" />
                  Compare…
                </DropdownMenuItem>
              )}
              {showDelete && version && (
                <>
                  {(showPromote || showCompare) && <DropdownMenuSeparator />}
                  <DropdownMenuItem
                    disabled={mutationInFlight}
                    onSelect={() => onDelete(version)}
                    className="text-destructive focus:text-destructive"
                  >
                    <Trash2Icon className="mr-2 size-4" />
                    Delete
                  </DropdownMenuItem>
                </>
              )}
            </DropdownMenuContent>
          </DropdownMenu>
        )}
      </TableCell>
    </TableRow>
  )
}

function VersionStatus({ row }: { row: VersionRow }) {
  if (row.isCurrent) {
    return <Badge variant="default">Current</Badge>
  }
  if (row.version) {
    return <Badge variant="secondary">Synced</Badge>
  }
  return <span className="text-xs text-muted-foreground">Not synced</span>
}

/** Part of the version name that isn't the bare commit SHA, if any. */
function getVersionSuffix(version: RegistryVersionRead): string | null {
  const { commit_sha: commitSha, version: name } = version
  if (!commitSha || name === commitSha) {
    return null
  }
  if (name.startsWith(commitSha)) {
    return name.slice(commitSha.length)
  }
  return shortVersion(name)
}
