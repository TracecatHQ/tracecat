"use client"

import React, { useState } from "react"
import { RegistryRepositoryReadMinimal } from "@/client"
import { DropdownMenuLabel } from "@radix-ui/react-dropdown-menu"
import { DotsHorizontalIcon } from "@radix-ui/react-icons"
import { Row } from "@tanstack/react-table"
import { CopyIcon, RefreshCcw } from "lucide-react"

import { useRegistryRepositories } from "@/lib/hooks"
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
  const { registryRepos, registryReposIsLoading, registryReposError } =
    useRegistryRepositories()
  const [selectedRepo, setSelectedRepo] =
    useState<RegistryRepositoryReadMinimal | null>(null)
  const handleOnClickRow = (row: Row<RegistryRepositoryReadMinimal>) => () => {
    // Link to workflow detail page
    console.debug("Clicked row", row)
    // router.push(`/registry/actions/${row.original.id}`) // view the schema table?
    setSelectedRepo(row.original)
  }

  const handleReloadRepository = (origin: string) => {
    console.log("Reloading repository", origin)
  }
  return (
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
                    <DotsHorizontalIcon className="size-4" />
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
                      navigator.clipboard.writeText(row.original.origin)
                      handleReloadRepository(row.original.origin)
                      toast({
                        title: "Reloading repository",
                        description: (
                          <div className="flex flex-col space-y-2">
                            <span>
                              Reloading actions from{" "}
                              <b className="inline-block">
                                {row.original.origin}
                              </b>
                            </span>
                            <span className="text-muted-foreground">
                              Repository URL: {row.original.origin}
                            </span>
                          </div>
                        ),
                      })
                    }}
                  >
                    <RefreshCcw className="mr-2 size-4" />
                    <span>Reload repository</span>
                  </DropdownMenuItem>
                </DropdownMenuContent>
              </DropdownMenu>
            )
          },
        },
      ]}
      toolbarProps={defaultToolbarProps}
    />
  )
}
const defaultToolbarProps: DataTableToolbarProps = {
  filterProps: {
    placeholder: "Search repositories...",
    column: "origin",
  },
}
