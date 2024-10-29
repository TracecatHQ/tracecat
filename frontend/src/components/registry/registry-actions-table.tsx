"use client"

import React, { useMemo, useState } from "react"
import { useRouter } from "next/navigation"
import { RegistryActionRead } from "@/client"
import { DropdownMenuLabel } from "@radix-ui/react-dropdown-menu"
import { DotsHorizontalIcon } from "@radix-ui/react-icons"
import { Row } from "@tanstack/react-table"
import { CopyIcon, Edit2Icon, FilePlusIcon, TrashIcon } from "lucide-react"

import { useRegistryActions } from "@/lib/hooks"
import { Button } from "@/components/ui/button"
import {
  DropdownMenu,
  DropdownMenuContent,
  DropdownMenuItem,
  DropdownMenuTrigger,
} from "@/components/ui/dropdown-menu"
import { toast } from "@/components/ui/use-toast"
import {
  DeleteRegistryActionAlertDialog,
  DeleteRegistryActionAlertDialogTrigger,
} from "@/components/registry/delete-registry-action"
import {
  DataTable,
  DataTableColumnHeader,
  type DataTableToolbarProps,
} from "@/components/table"

export function RegistryActionsTable() {
  const router = useRouter()
  const { registryActions, registryActionsIsLoading, registryActionsError } =
    useRegistryActions()
  const [selectedAction, setSelectedAction] =
    useState<RegistryActionRead | null>(null)

  // Create a memoized version of the toolbar props
  const toolbarProps: DataTableToolbarProps = useMemo(() => {
    // Extract unique namespace values from the data
    const namespaceOptions = Array.from(
      new Set(registryActions?.map((action) => action.namespace))
    )
      .sort() // Sort namespaces alphabetically
      .map((namespace) => ({
        label: namespace,
        value: namespace,
      }))
    const typeOptions = Array.from(
      new Set(registryActions?.map((action) => action.type))
    )
      .sort() // Sort types alphabetically
      .map((type) => ({
        label: type,
        value: type,
      }))

    const originOptions = Array.from(
      new Set(registryActions?.map((action) => action.origin))
    )
      .sort() // Sort types alphabetically
      .map((origin) => ({
        label: origin,
        value: origin,
      }))

    return {
      filterProps: {
        placeholder: "Search actions...",
        column: "default_title",
      },
      fields: [
        {
          column: "type",
          title: "Type",
          options: typeOptions,
        },
        {
          column: "namespace",
          title: "Namespace",
          options: namespaceOptions,
        },
        {
          column: "origin",
          title: "Origin",
          options: originOptions,
        },
      ],
    }
  }, [registryActions])

  const handleOnClickRow = (row: Row<RegistryActionRead>) => () => {
    // Link to workflow detail page
    console.debug("Clicked row", row)
    setSelectedAction(row.original)
  }
  return (
    <DeleteRegistryActionAlertDialog
      selectedAction={selectedAction}
      setSelectedAction={setSelectedAction}
    >
      <DataTable
        isLoading={registryActionsIsLoading}
        error={registryActionsError ?? undefined}
        data={registryActions}
        emptyMessage="No actions found."
        errorMessage="Error loading workflows."
        onClickRow={handleOnClickRow}
        columns={[
          {
            accessorKey: "default_title",
            header: ({ column }) => (
              <DataTableColumnHeader
                className="text-xs"
                column={column}
                title="Title"
              />
            ),
            cell: ({ row }) => (
              <div className="text-xs text-foreground/80">
                {row.getValue<RegistryActionRead["default_title"]>(
                  "default_title"
                )}
              </div>
            ),
            enableSorting: true,
            enableHiding: false,
          },
          {
            accessorKey: "namespace",
            header: ({ column }) => (
              <DataTableColumnHeader
                className="text-xs"
                column={column}
                title="Action Name"
              />
            ),
            cell: ({ row }) => (
              <div className="font-mono text-xs tracking-tight text-foreground/80">
                {row.original.action}
              </div>
            ),
            enableSorting: true,
            enableHiding: false,
            enableColumnFilter: true,
            filterFn: (row, id, value) => {
              return value.includes(
                row.getValue<RegistryActionRead["namespace"]>(id)
              )
            },
          },
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
                  {row.getValue<RegistryActionRead["origin"]>("origin") || "-"}
                </div>
              )
            },
            enableSorting: true,
            enableHiding: false,
            enableColumnFilter: true,
            filterFn: (row, id, value) => {
              return value.includes(
                row.getValue<RegistryActionRead["origin"]>(id)
              )
            },
          },
          {
            accessorKey: "type",
            header: ({ column }) => (
              <DataTableColumnHeader
                className="text-xs"
                column={column}
                title="Type"
              />
            ),
            cell: ({ row }) => {
              return (
                <div className="text-xs text-foreground/80">
                  {row.getValue<RegistryActionRead["type"]>("type")}
                </div>
              )
            },
            enableSorting: true,
            enableHiding: false,
            enableColumnFilter: true,
            filterFn: (row, id, value) => {
              return value.includes(
                row.getValue<RegistryActionRead["type"]>(id)
              )
            },
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
                    {row.original.is_template && (
                      <>
                        <DropdownMenuLabel className="p-2 text-xs font-semibold text-muted-foreground">
                          Templates
                        </DropdownMenuLabel>
                        <DropdownMenuItem
                          className="flex items-center text-xs"
                          onClick={async (e) => {
                            e.stopPropagation() // Prevent row click
                            // popup a dialog to create a new  from this template
                            router.push(
                              // NOTE: This is hardcoded to the custom origin. Maybe put this in an API call later.
                              `/registry/actions/new?template=${row.original.action}&origin=custom`
                            )
                          }}
                        >
                          <FilePlusIcon className="mr-2 size-4" />
                          <span>New from template</span>
                        </DropdownMenuItem>
                        <DropdownMenuItem
                          className="flex items-center text-xs"
                          onClick={async (e) => {
                            e.stopPropagation() // Prevent row click
                            router.push(
                              `/registry/actions/edit?template=${row.original.action}&origin=${row.original.origin}`
                            )
                          }}
                        >
                          <Edit2Icon className="mr-2 size-4" />
                          <span>Edit template</span>
                        </DropdownMenuItem>
                      </>
                    )}
                    <DropdownMenuLabel className="p-2 text-xs font-semibold text-muted-foreground">
                      Actions
                    </DropdownMenuLabel>
                    <DropdownMenuItem
                      className="flex items-center text-xs"
                      onClick={(e) => {
                        e.stopPropagation() // Prevent row click
                        navigator.clipboard.writeText(row.original.action)
                        toast({
                          title: "Action name copied",
                          description: (
                            <div className="flex flex-col space-y-2">
                              <span>
                                Action name copied for{" "}
                                <b className="inline-block">
                                  {row.original.default_title}
                                </b>
                              </span>
                              <span className="text-muted-foreground">
                                Action name: {row.original.action}
                              </span>
                            </div>
                          ),
                        })
                      }}
                    >
                      <CopyIcon className="mr-2 size-4" />
                      <span>Copy action name</span>
                    </DropdownMenuItem>
                    <DeleteRegistryActionAlertDialogTrigger asChild>
                      <DropdownMenuItem
                        className="text-xs text-rose-500 focus:text-rose-600"
                        onClick={() => {
                          setSelectedAction(row.original)
                          console.debug(
                            "Selected action to delete",
                            row.original
                          )
                        }}
                      >
                        <TrashIcon className="mr-2 size-4 text-rose-500" />
                        <span className="text-rose-500">Delete action</span>
                      </DropdownMenuItem>
                    </DeleteRegistryActionAlertDialogTrigger>
                  </DropdownMenuContent>
                </DropdownMenu>
              )
            },
          },
        ]}
        toolbarProps={toolbarProps}
      />
    </DeleteRegistryActionAlertDialog>
  )
}
