"use client"

import React, { useCallback, useState } from "react"
import { RegistryErrorResponse, RegistryRepositoryReadMinimal } from "@/client"
import { DropdownMenuLabel } from "@radix-ui/react-dropdown-menu"
import { DotsHorizontalIcon } from "@radix-ui/react-icons"
import {
  AlertTriangleIcon,
  ChevronDownIcon,
  CopyIcon,
  CornerDownRightIcon,
  LoaderCircleIcon,
  RefreshCcw,
  Trash2Icon,
  TrashIcon,
} from "lucide-react"

import { getRelativeTime } from "@/lib/event-history"
import { useRegistryRepositories } from "@/lib/hooks"
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
import { TooltipProvider } from "@/components/ui/tooltip"
import { toast } from "@/components/ui/use-toast"
import {
  DataTable,
  DataTableColumnHeader,
  type DataTableToolbarProps,
} from "@/components/table"

enum AlertAction {
  SYNC,
  DELETE,
  SYNC_EXECUTOR,
}

export function RegistryRepositoriesTable() {
  const {
    repos: registryRepos,
    reposIsLoading: registryReposIsLoading,
    reposError: registryReposError,
    syncRepo,
    syncRepoIsPending,
    deleteRepo,
    syncRepoError,
  } = useRegistryRepositories()
  const [selectedRepo, setSelectedRepo] =
    useState<RegistryRepositoryReadMinimal | null>(null)
  const [alertAction, setAlertAction] = useState<AlertAction | null>(null)
  const [alertOpen, setAlertOpen] = useState(false)
  const getAlertContent = useCallback(() => {
    switch (alertAction) {
      case AlertAction.SYNC:
        return {
          title: "Sync repository",
          description: (
            <div className="flex flex-col space-y-2">
              <span>
                You are about to pull the latest version of the repository{" "}
              </span>
              <b className="font-mono tracking-tighter">
                {selectedRepo?.origin}
              </b>
              {selectedRepo?.commit_sha && (
                <div className="text-sm text-muted-foreground">
                  <span>Current SHA: </span>
                  <Badge className="font-mono text-xs" variant="secondary">
                    {selectedRepo.commit_sha}
                  </Badge>
                </div>
              )}
              {selectedRepo?.last_synced_at && (
                <div className="text-sm text-muted-foreground">
                  <span>Last synced: </span>
                  <span>
                    {new Date(selectedRepo.last_synced_at).toLocaleString()}
                  </span>
                </div>
              )}
              <p>
                Are you sure you want to proceed? This will reload all existing
                actions with the latest versions from the remote repository.
              </p>
            </div>
          ),
          actions: [
            {
              label: (
                <div className="flex items-center space-x-2">
                  <RefreshCcw className="size-4" />
                  <span>Sync</span>
                </div>
              ),
              action: async () => {
                if (!selectedRepo) {
                  console.error("No repository selected")
                  return
                }
                console.log("Reloading repository", selectedRepo.origin)
                try {
                  await syncRepo({ repositoryId: selectedRepo.id })
                  toast({
                    title: "Successfully synced repository",
                    description: (
                      <div className="flex flex-col space-y-2">
                        <div>
                          Successfully reloaded actions from{" "}
                          <b className="inline-block">{selectedRepo.origin}</b>
                        </div>
                      </div>
                    ),
                  })
                } catch (error) {
                  console.error("Error reloading repository", error)
                } finally {
                  setSelectedRepo(null)
                }
              },
            },
          ],
        }
      case AlertAction.DELETE:
        return {
          title: "Delete repository",
          description: (
            <div className="flex flex-col space-y-2">
              <span>You are about to delete the repository </span>
              <b className="font-mono tracking-tighter">
                {selectedRepo?.origin}
              </b>
              <p>
                Are you sure you want to proceed? This action cannot be undone.
              </p>
              <p className="italic">
                You cannot delete the base Tracecat actions or the custom
                template repositories. If you delete your remote repository, you
                will need to restart the instance to restore it.
              </p>
            </div>
          ),
          actions: [
            {
              label: (
                <div className="flex items-center space-x-2">
                  <Trash2Icon className="size-4" />
                  <span>Delete</span>
                </div>
              ),
              action: async () => {
                if (!selectedRepo) {
                  console.error("No repository selected")
                  return
                }
                console.log(
                  "Deleting repository",
                  selectedRepo.origin,
                  selectedRepo.id
                )
                try {
                  await deleteRepo({ repositoryId: selectedRepo.id })
                } catch (error) {
                  console.error("Error deleting repository", error)
                } finally {
                  setSelectedRepo(null)
                }
              },
            },
          ],
        }
      default:
        return null
    }
  }, [alertAction, selectedRepo])

  const alertContent = getAlertContent()

  const errorDetail = syncRepoError?.body.detail as RegistryErrorResponse | null
  return (
    <TooltipProvider>
      {errorDetail && (
        <div className="space-y-2 rounded-md border border-rose-400 bg-rose-100 p-2 font-mono tracking-tighter">
          <Collapsible>
            <CollapsibleTrigger asChild className="group">
              <div className="flex cursor-pointer items-center justify-between text-sm font-bold text-rose-500">
                <div className="flex items-center">
                  <AlertTriangleIcon className="mr-2 size-4 fill-red-500 stroke-white" />
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
                                  {error.is_template ? (
                                    <span>
                                      <span className="font-semibold text-foreground/50">
                                        steps.{error.step_ref}{" "}
                                      </span>
                                      <span className="text-muted-foreground">
                                        ({error.step_action})
                                      </span>
                                    </span>
                                  ) : (
                                    <span>steps.${error.step_ref}</span>
                                  )}
                                </span>
                              </div>
                              {error.details.map((detail, detailIndex) => (
                                <p
                                  key={`${detail}-${errorIndex}-${detailIndex}`}
                                  className="ml-4 flex items-center text-foreground/60"
                                >
                                  <CornerDownRightIcon className="mr-2 size-3" />
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
      <AlertDialog open={alertOpen}>
        <DataTable
          isLoading={registryReposIsLoading}
          error={registryReposError ?? undefined}
          data={registryRepos}
          emptyMessage="No actions found."
          errorMessage="Error loading workflows."
          columns={[
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
                if (!sha)
                  return <div className="text-xs text-foreground/80">-</div>
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
              cell: ({ row }) => {
                const commitSha = row.original.commit_sha
                const isSelected = row.original.id === selectedRepo?.id
                const Icon = () => {
                  if (isSelected && syncRepoIsPending) {
                    return <LoaderCircleIcon className="size-4 animate-spin" />
                  }
                  const errorDetail = syncRepoError?.body
                    .detail as RegistryErrorResponse
                  if (errorDetail?.id === row.original.id) {
                    return (
                      <AlertTriangleIcon className="size-4 fill-rose-600 text-white" />
                    )
                  }
                  return <DotsHorizontalIcon className="size-4" />
                }
                return (
                  <DropdownMenu>
                    <DropdownMenuTrigger asChild>
                      <Button
                        variant="ghost"
                        className="size-8 p-0"
                        onClick={(e) => e.stopPropagation()} // Prevent row click
                      >
                        <span className="sr-only">Open menu</span>
                        <Icon />
                      </Button>
                    </DropdownMenuTrigger>
                    <DropdownMenuContent align="end">
                      <DropdownMenuLabel className="p-2 text-xs font-semibold text-muted-foreground">
                        Actions
                      </DropdownMenuLabel>

                      <DropdownMenuItem
                        className="flex items-center text-xs"
                        onClick={(e) => {
                          e.stopPropagation() // Prevent row click
                          navigator.clipboard.writeText(row.original.origin)
                          toast({
                            title: "Repository origin copied",
                            description: (
                              <div className="flex flex-col space-y-2">
                                <span className="inline-block">
                                  {row.original.origin}
                                </span>
                              </div>
                            ),
                          })
                        }}
                      >
                        <CopyIcon className="mr-2 size-4" />
                        <span>Copy repository origin</span>
                      </DropdownMenuItem>
                      {commitSha !== null && (
                        <DropdownMenuItem
                          className="flex items-center text-xs"
                          onClick={(e) => {
                            e.stopPropagation() // Prevent row click
                            navigator.clipboard.writeText(commitSha)
                            toast({
                              title: "Commit SHA copied",
                              description: (
                                <div className="flex flex-col space-y-2">
                                  <span className="inline-block">
                                    {commitSha}
                                  </span>
                                </div>
                              ),
                            })
                          }}
                        >
                          <CopyIcon className="mr-2 size-4" />
                          <span>Copy commit SHA</span>
                        </DropdownMenuItem>
                      )}
                      <DropdownMenuItem
                        className="flex items-center text-xs"
                        onClick={(e) => {
                          e.stopPropagation() // Prevent row click
                          setSelectedRepo(row.original)
                          setAlertAction(AlertAction.SYNC)
                          setAlertOpen(true)
                        }}
                      >
                        <RefreshCcw className="mr-2 size-4" />
                        <span>Sync from remote</span>
                      </DropdownMenuItem>
                      <DropdownMenuItem
                        className="flex items-center text-xs text-rose-600"
                        onClick={(e) => {
                          e.stopPropagation() // Prevent row click
                          setSelectedRepo(row.original)
                          setAlertAction(AlertAction.DELETE)
                          setAlertOpen(true)
                        }}
                      >
                        <TrashIcon className="mr-2 size-4" />
                        <span>Delete repository</span>
                      </DropdownMenuItem>
                    </DropdownMenuContent>
                  </DropdownMenu>
                )
              },
            },
          ]}
          toolbarProps={defaultToolbarProps}
        />
        <AlertDialogContent>
          <AlertDialogHeader>
            <AlertDialogTitle>{alertContent?.title}</AlertDialogTitle>
            <AlertDialogDescription>
              {alertContent?.description}
            </AlertDialogDescription>
          </AlertDialogHeader>
          <AlertDialogFooter>
            <AlertDialogCancel onClick={() => setAlertOpen(false)}>
              Cancel
            </AlertDialogCancel>

            {alertContent?.actions.map((action, index) => (
              <AlertDialogAction
                key={index}
                onClick={async () => {
                  setAlertOpen(false)
                  await action.action()
                }}
                disabled={syncRepoIsPending}
              >
                {action.label}
              </AlertDialogAction>
            ))}
          </AlertDialogFooter>
        </AlertDialogContent>
      </AlertDialog>
    </TooltipProvider>
  )
}
const defaultToolbarProps: DataTableToolbarProps = {
  filterProps: {
    placeholder: "Search repositories...",
    column: "origin",
  },
}
