"use client"

import { DotsHorizontalIcon } from "@radix-ui/react-icons"
import type { ColumnDef } from "@tanstack/react-table"
import {
  AlertTriangleIcon,
  ChevronDownIcon,
  CornerDownRightIcon,
} from "lucide-react"
import { useMemo, useState } from "react"
import type {
  RegistryRepositoryErrorDetail,
  RegistryRepositoryReadMinimal,
} from "@/client"
import {
  DataTable,
  DataTableColumnHeader,
  type DataTableToolbarProps,
} from "@/components/data-table"
import { CommitSelectorDialog } from "@/components/registry/dialogs/repository-commit-dialog"
import { DeleteRepositoryDialog } from "@/components/registry/dialogs/repository-delete-dialog"
import { SyncRepositoryDialog } from "@/components/registry/dialogs/repository-sync-dialog"
import { RegistryTableActiveDialog } from "@/components/registry/registry-common"
import { RepositoryActions } from "@/components/registry/table-actions"
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
  DropdownMenuTrigger,
} from "@/components/ui/dropdown-menu"
import { useAuth } from "@/hooks/use-auth"
import { useOrgMembership } from "@/hooks/use-org-membership"
import { getRelativeTime } from "@/lib/event-history"
import { useRegistryRepositories } from "@/lib/hooks"

export function RegistryRepositoriesTable() {
  const { user } = useAuth()
  const { hasOrgAdminRole } = useOrgMembership()
  const canAdministerOrg = user?.isPlatformAdmin() || hasOrgAdminRole
  const {
    repos: registryRepos,
    reposIsLoading: registryReposIsLoading,
    reposError: registryReposError,
    syncRepo,
    syncRepoIsPending,
    syncRepoError,
  } = useRegistryRepositories()

  // Dialog state management
  const [activeDialog, setActiveDialog] =
    useState<RegistryTableActiveDialog | null>(null)
  const [selectedRepo, setSelectedRepo] =
    useState<RegistryRepositoryReadMinimal | null>(null)

  const onOpenChange = (open: boolean) => {
    if (!open) {
      setActiveDialog(null)
      setSelectedRepo(null)
    }
  }

  const errorDetail =
    syncRepoError?.status === 422
      ? (syncRepoError?.body.detail as RegistryRepositoryErrorDetail)
      : null

  const columns: ColumnDef<RegistryRepositoryReadMinimal>[] = useMemo(
    () => [
      {
        accessorKey: "origin",
        header: ({ column }) => (
          <DataTableColumnHeader
            className="text-xs"
            column={column}
            title="Origin"
          />
        ),
        cell: ({ row }) => {
          return (
            <div className="text-xs text-foreground/80">
              {row.getValue<RegistryRepositoryReadMinimal["origin"]>(
                "origin"
              ) || "-"}
            </div>
          )
        },
        enableSorting: true,
        enableHiding: false,
      },
      {
        accessorKey: "commit_sha",
        header: ({ column }) => (
          <DataTableColumnHeader
            className="text-xs"
            column={column}
            title="Commit SHA"
          />
        ),
        cell: ({ row }) => {
          const sha =
            row.getValue<RegistryRepositoryReadMinimal["commit_sha"]>(
              "commit_sha"
            )
          if (!sha) return <div className="text-xs text-foreground/80">-</div>
          return (
            <Badge
              className="font-mono text-xs font-normal"
              variant="secondary"
            >
              {sha.substring(0, 7)}
            </Badge>
          )
        },
        enableHiding: false,
      },
      {
        accessorKey: "last_synced_at",
        header: ({ column }) => (
          <DataTableColumnHeader
            className="text-xs"
            column={column}
            title="Last synced"
          />
        ),
        cell: ({ row }) => {
          const lastSyncedAt =
            row.getValue<RegistryRepositoryReadMinimal["last_synced_at"]>(
              "last_synced_at"
            )
          if (!lastSyncedAt) {
            return <div className="text-xs text-foreground/80">-</div>
          }
          const date = new Date(lastSyncedAt)
          const ago = getRelativeTime(date)
          return (
            <div className="space-x-2 text-xs">
              <span>{date.toLocaleString()}</span>
              <span className="text-muted-foreground">({ago})</span>
            </div>
          )
        },
        enableSorting: true,
        enableHiding: false,
      },
      {
        id: "actions",
        enableHiding: false,
        cell: ({ row }) => (
          <DropdownMenu>
            <DropdownMenuTrigger asChild>
              <Button
                variant="ghost"
                className="size-8 p-0"
                onClick={(e) => e.stopPropagation()} // Prevent row click
              >
                <span className="sr-only">Open menu</span>
                <DotsHorizontalIcon className="size-4" />
              </Button>
            </DropdownMenuTrigger>
            <DropdownMenuContent>
              {canAdministerOrg && (
                <RepositoryActions
                  repository={row.original}
                  onSync={() => {
                    setSelectedRepo(row.original)
                    setActiveDialog(RegistryTableActiveDialog.RepositorySync)
                  }}
                  onDelete={() => {
                    setSelectedRepo(row.original)
                    setActiveDialog(RegistryTableActiveDialog.RepositoryDelete)
                  }}
                  onChangeCommit={() => {
                    setSelectedRepo(row.original)
                    setActiveDialog(RegistryTableActiveDialog.RepositoryCommit)
                  }}
                />
              )}
            </DropdownMenuContent>
          </DropdownMenu>
        ),
      },
    ],
    [user]
  )

  return (
    <>
      {errorDetail && (
        <div className="space-y-2 rounded-md border border-rose-400 bg-rose-100 p-2 font-mono tracking-tighter">
          <Collapsible>
            <CollapsibleTrigger asChild className="group">
              <div className="flex cursor-pointer items-center justify-between text-sm font-bold text-rose-500">
                <div className="flex items-center">
                  <AlertTriangleIcon className="mr-2 size-4 fill-red-500 stroke-rose-200" />
                  <span>{errorDetail.message}</span>
                </div>
                <ChevronDownIcon className="size-4 group-data-[state=closed]:rotate-0 group-data-[state=open]:rotate-180" />
              </div>
            </CollapsibleTrigger>
            <CollapsibleContent className="border-rose-400 text-sm">
              <div className="mt-1 max-h-[50vh] space-y-1 overflow-y-auto">
                <div className="flex flex-col space-y-4 text-foreground/60">
                  {Object.entries(errorDetail.errors).map(([key, errors]) => (
                    <div key={key} className="flex flex-col space-y-1">
                      <span className="font-mono font-semibold tracking-tighter text-foreground/60">
                        {key}
                      </span>
                      <div className="flex flex-col">
                        {errors.map((error, errorIndex) => {
                          return (
                            <div key={errorIndex}>
                              <div className="flex items-center">
                                <span className="mx-2 inline-block size-1 rounded-full bg-current" />
                                <span className="font-mono tracking-tighter">
                                  <span className="font-semibold text-foreground/50">
                                    {error.loc_primary}{" "}
                                  </span>
                                  {error.loc_secondary && (
                                    <span className="text-muted-foreground">
                                      ({error.loc_secondary})
                                    </span>
                                  )}
                                </span>
                              </div>
                              {error.details.map((detail, detailIndex) => (
                                <p
                                  key={`${detail}-${errorIndex}-${detailIndex}`}
                                  className="ml-4 flex items-start text-foreground/60"
                                >
                                  <CornerDownRightIcon className="mr-2 mt-0.5 size-3" />
                                  <span>{detail}</span>
                                </p>
                              ))}
                            </div>
                          )
                        })}
                      </div>
                    </div>
                  ))}
                </div>
              </div>
            </CollapsibleContent>
          </Collapsible>
        </div>
      )}
      <DataTable
        isLoading={registryReposIsLoading}
        error={registryReposError ?? undefined}
        data={registryRepos}
        emptyMessage="No repositories found."
        errorMessage="Error loading workflows."
        columns={columns}
        toolbarProps={defaultToolbarProps}
      />

      {/* Dialog Components */}
      <SyncRepositoryDialog
        open={activeDialog === RegistryTableActiveDialog.RepositorySync}
        onOpenChange={onOpenChange}
        selectedRepo={selectedRepo}
        setSelectedRepo={setSelectedRepo}
        syncRepo={syncRepo}
        syncRepoIsPending={syncRepoIsPending}
      />

      <DeleteRepositoryDialog
        open={activeDialog === RegistryTableActiveDialog.RepositoryDelete}
        onOpenChange={onOpenChange}
        selectedRepo={selectedRepo}
        setSelectedRepo={setSelectedRepo}
      />

      <CommitSelectorDialog
        open={activeDialog === RegistryTableActiveDialog.RepositoryCommit}
        onOpenChange={onOpenChange}
        selectedRepo={selectedRepo}
        initialCommitSha={selectedRepo?.commit_sha}
      />
    </>
  )
}
const defaultToolbarProps: DataTableToolbarProps<RegistryRepositoryReadMinimal> =
  {
    filterProps: {
      placeholder: "Search repositories...",
      column: "origin",
    },
  }
