"use client"

import React, { useCallback, useState } from "react"
import { RegistryRepositoryReadMinimal } from "@/client"
import { DropdownMenuLabel } from "@radix-ui/react-dropdown-menu"
import { DotsHorizontalIcon } from "@radix-ui/react-icons"
import { CopyIcon, LoaderCircleIcon, RefreshCcw, TrashIcon } from "lucide-react"

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
import { Button } from "@/components/ui/button"
import {
  DropdownMenu,
  DropdownMenuContent,
  DropdownMenuItem,
  DropdownMenuTrigger,
} from "@/components/ui/dropdown-menu"
import { toast } from "@/components/ui/use-toast"
import {
  DataTable,
  DataTableColumnHeader,
  type DataTableToolbarProps,
} from "@/components/table"

enum AlertAction {
  SYNC,
  DELETE,
}

export function RegistryRepositoriesTable() {
  const {
    registryRepos,
    registryReposIsLoading,
    registryReposError,
    syncRepos,
    syncReposIsPending,
    deleteRepo,
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
            <>
              <span>You are about to sync the repository </span>
              <b className="font-mono tracking-tighter">
                {selectedRepo?.origin}
              </b>
              <p>
                Are you sure you want to proceed? This will reload all existing
                actions with the latest versions from the repository.
              </p>
            </>
          ),
          action: async () => {
            if (!selectedRepo) {
              console.error("No repository selected")
              return
            }
            console.log("Reloading repository", selectedRepo.origin)
            try {
              await syncRepos({ origins: [selectedRepo.origin] })
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
              await deleteRepo({ id: selectedRepo.id })
            } catch (error) {
              console.error("Error deleting repository", error)
            } finally {
              setSelectedRepo(null)
            }
          },
        }
      default:
        return null
    }
  }, [alertAction, selectedRepo])

  const alertContent = getAlertContent()

  return (
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
            id: "actions",
            enableHiding: false,
            cell: ({ row }) => {
              return (
                <DropdownMenu>
                  <DropdownMenuTrigger asChild>
                    <Button
                      variant="ghost"
                      className="size-8 p-0"
                      onClick={(e) => e.stopPropagation()} // Prevent row click
                    >
                      <span className="sr-only">Open menu</span>
                      {row.original.origin === selectedRepo?.origin &&
                      syncReposIsPending ? (
                        <LoaderCircleIcon className="size-4 animate-spin" />
                      ) : (
                        <DotsHorizontalIcon className="size-4" />
                      )}
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
                              <span>
                                Repository origin copied for{" "}
                                <b className="inline-block">
                                  {row.original.origin}
                                </b>
                              </span>
                              <span className="text-muted-foreground">
                                Repository origin: {row.original.origin}
                              </span>
                            </div>
                          ),
                        })
                      }}
                    >
                      <CopyIcon className="mr-2 size-4" />
                      <span>Copy repository origin</span>
                    </DropdownMenuItem>
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
                      <span>Sync repository</span>
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
          <AlertDialogAction
            onClick={async () => {
              setAlertOpen(false)
              await alertContent?.action()
            }}
            disabled={syncReposIsPending}
          >
            Confirm
          </AlertDialogAction>
        </AlertDialogFooter>
      </AlertDialogContent>
    </AlertDialog>
  )
}
const defaultToolbarProps: DataTableToolbarProps = {
  filterProps: {
    placeholder: "Search repositories...",
    column: "origin",
  },
}
