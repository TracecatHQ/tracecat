"use client"

import React, { useState } from "react"
import { RegistryRepositoryReadMinimal } from "@/client"
import { DropdownMenuLabel } from "@radix-ui/react-dropdown-menu"
import { DotsHorizontalIcon } from "@radix-ui/react-icons"
import { Row } from "@tanstack/react-table"
import { CopyIcon, LoaderCircleIcon, RefreshCcw } from "lucide-react"

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
  AlertDialogTrigger,
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

export function RegistryRepositoriesTable() {
  const {
    registryRepos,
    registryReposIsLoading,
    registryReposError,
    syncRepos,
    syncReposIsPending,
  } = useRegistryRepositories()
  const [selectedRepo, setSelectedRepo] =
    useState<RegistryRepositoryReadMinimal | null>(null)
  const handleOnClickRow = (row: Row<RegistryRepositoryReadMinimal>) => () => {
    // Link to workflow detail page
    console.debug("Clicked row", row)
    // router.push(`/registry/actions/${row.original.id}`) // view the schema table?
    setSelectedRepo(row.original)
  }

  const handleReloadRepository = async (origin: string) => {
    console.log("Reloading repository", origin)
    try {
      await syncRepos({ origins: [origin] })
      toast({
        title: "Successfully synced repository",
        description: (
          <div className="flex flex-col space-y-2">
            <span>
              Successfully reloaded actions from{" "}
              <b className="inline-block">{origin}</b>
            </span>
          </div>
        ),
      })
    } catch (error) {
      console.error("Error reloading repository", error)
    } finally {
      setSelectedRepo(null)
    }
  }
  return (
    <AlertDialog>
      <DataTable
        isLoading={registryReposIsLoading}
        error={registryReposError ?? undefined}
        data={registryRepos}
        emptyMessage="No actions found."
        errorMessage="Error loading workflows."
        onClickRow={handleOnClickRow}
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
                    <AlertDialogTrigger asChild>
                      <DropdownMenuItem
                        className="flex items-center text-xs"
                        onClick={(e) => {
                          e.stopPropagation() // Prevent row click
                          setSelectedRepo(row.original)
                        }}
                      >
                        <RefreshCcw className="mr-2 size-4" />
                        <span>Sync repository</span>
                      </DropdownMenuItem>
                    </AlertDialogTrigger>
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
          <AlertDialogTitle>Sync repository</AlertDialogTitle>
          <AlertDialogDescription className="flex flex-col space-y-2">
            <span>You are about to sync the repository </span>
            <b className="font-mono tracking-tighter">{selectedRepo?.origin}</b>
          </AlertDialogDescription>
          <AlertDialogDescription>
            Are you sure you want to proceed? This will reload all existing
            actions with the latest versions from the repository.
          </AlertDialogDescription>
        </AlertDialogHeader>
        <AlertDialogFooter>
          <AlertDialogCancel>Cancel</AlertDialogCancel>
          <AlertDialogAction
            onClick={async () => {
              if (!selectedRepo) {
                console.error("No repository selected")
                return
              }
              await handleReloadRepository(selectedRepo.origin)
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
