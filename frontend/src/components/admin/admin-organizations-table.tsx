"use client"

import { DotsHorizontalIcon } from "@radix-ui/react-icons"
import Cookies from "js-cookie"
import Link from "next/link"
import { useState } from "react"
import type { OrgRead } from "@/client"
import {
  DataTable,
  DataTableColumnHeader,
  type DataTableToolbarProps,
} from "@/components/data-table"
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
import { Badge } from "@/components/ui/badge"
import { Button } from "@/components/ui/button"
import {
  DropdownMenu,
  DropdownMenuContent,
  DropdownMenuItem,
  DropdownMenuSeparator,
  DropdownMenuTrigger,
} from "@/components/ui/dropdown-menu"
import { useAdminOrganizations } from "@/hooks/use-admin"

export function AdminOrganizationsTable() {
  const [selectedOrg, setSelectedOrg] = useState<OrgRead | null>(null)
  const { organizations, deleteOrganization } = useAdminOrganizations()

  const handleDeleteOrganization = async () => {
    if (selectedOrg) {
      try {
        await deleteOrganization(selectedOrg.id)
      } catch (error) {
        console.error("Failed to delete organization", error)
      } finally {
        setSelectedOrg(null)
      }
    }
  }

  const handleEnterOrganization = (orgId: string) => {
    // Set the org override cookie
    Cookies.set("tracecat-org-id", orgId, { path: "/", sameSite: "lax" })
    // Clear the last-viewed workspace cookie to avoid redirecting to a workspace from another org
    Cookies.remove("__tracecat:workspaces:last-viewed", { path: "/" })
    // Force a full page reload to clear React Query cache
    window.location.href = "/workspaces"
  }

  return (
    <AlertDialog
      onOpenChange={(isOpen) => {
        if (!isOpen) {
          setSelectedOrg(null)
        }
      }}
    >
      <DataTable
        data={organizations ?? []}
        initialSortingState={[{ id: "name", desc: false }]}
        columns={[
          {
            accessorKey: "name",
            header: ({ column }) => (
              <DataTableColumnHeader
                className="text-xs"
                column={column}
                title="Name"
              />
            ),
            cell: ({ row }) => (
              <div className="text-xs font-medium">
                <Link
                  href={`/admin/organizations/${row.original.id}`}
                  className="hover:underline"
                >
                  {row.getValue<OrgRead["name"]>("name")}
                </Link>
              </div>
            ),
            enableSorting: true,
            enableHiding: false,
          },
          {
            accessorKey: "slug",
            header: ({ column }) => (
              <DataTableColumnHeader
                className="text-xs"
                column={column}
                title="Slug"
              />
            ),
            cell: ({ row }) => (
              <div className="text-xs font-mono text-muted-foreground">
                {row.getValue<OrgRead["slug"]>("slug")}
              </div>
            ),
            enableSorting: true,
            enableHiding: false,
          },
          {
            accessorKey: "is_active",
            header: ({ column }) => (
              <DataTableColumnHeader
                className="text-xs"
                column={column}
                title="Active"
              />
            ),
            cell: ({ row }) => (
              <Badge
                variant={
                  row.getValue<OrgRead["is_active"]>("is_active")
                    ? "default"
                    : "secondary"
                }
              >
                {row.getValue<OrgRead["is_active"]>("is_active")
                  ? "Active"
                  : "Inactive"}
              </Badge>
            ),
            enableSorting: true,
            enableHiding: false,
          },
          {
            accessorKey: "created_at",
            header: ({ column }) => (
              <DataTableColumnHeader
                className="text-xs"
                column={column}
                title="Created"
              />
            ),
            cell: ({ row }) => {
              const createdAt =
                row.getValue<OrgRead["created_at"]>("created_at")
              const date = new Date(createdAt)
              return (
                <div className="text-xs text-muted-foreground">
                  {date.toLocaleDateString()}
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
                    <Button variant="ghost" className="size-8 p-0">
                      <span className="sr-only">Open menu</span>
                      <DotsHorizontalIcon className="size-4" />
                    </Button>
                  </DropdownMenuTrigger>
                  <DropdownMenuContent align="end">
                    <DropdownMenuItem
                      onClick={() =>
                        navigator.clipboard.writeText(row.original.id)
                      }
                    >
                      Copy ID
                    </DropdownMenuItem>
                    <DropdownMenuItem asChild>
                      <Link href={`/admin/organizations/${row.original.id}`}>
                        Edit organization
                      </Link>
                    </DropdownMenuItem>
                    <DropdownMenuItem asChild>
                      <Link
                        href={`/admin/organizations/${row.original.id}/tier`}
                      >
                        Manage tier
                      </Link>
                    </DropdownMenuItem>
                    <DropdownMenuItem asChild>
                      <Link
                        href={`/admin/organizations/${row.original.id}/registry`}
                      >
                        Registry
                      </Link>
                    </DropdownMenuItem>
                    <DropdownMenuSeparator />
                    <DropdownMenuItem
                      onSelect={() => handleEnterOrganization(row.original.id)}
                    >
                      Enter organization
                    </DropdownMenuItem>
                    <DropdownMenuSeparator />
                    <AlertDialogTrigger asChild>
                      <DropdownMenuItem
                        className="text-rose-500 focus:text-rose-600"
                        onClick={() => setSelectedOrg(row.original)}
                      >
                        Delete organization
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
          <AlertDialogTitle>Delete organization</AlertDialogTitle>
          <AlertDialogDescription>
            Are you sure you want to delete the organization &quot;
            {selectedOrg?.name}&quot;? This action cannot be undone and will
            delete all associated data.
          </AlertDialogDescription>
        </AlertDialogHeader>
        <AlertDialogFooter>
          <AlertDialogCancel>Cancel</AlertDialogCancel>
          <AlertDialogAction
            variant="destructive"
            onClick={handleDeleteOrganization}
          >
            Delete
          </AlertDialogAction>
        </AlertDialogFooter>
      </AlertDialogContent>
    </AlertDialog>
  )
}

const defaultToolbarProps: DataTableToolbarProps<OrgRead> = {
  filterProps: {
    placeholder: "Filter organizations...",
    column: "name",
  },
}
