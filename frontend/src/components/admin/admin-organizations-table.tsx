"use client"

import { DotsHorizontalIcon } from "@radix-ui/react-icons"
import Cookies from "js-cookie"
import { useState } from "react"
import type { tracecat_ee__admin__organizations__schemas__OrgRead as OrgRead } from "@/client"
import { AdminOrgDomainsDialog } from "@/components/admin/admin-org-domains-dialog"
import { AdminOrgRegistryDialog } from "@/components/admin/admin-org-registry-dialog"
import { AdminOrgTierDialog } from "@/components/admin/admin-org-tier-dialog"
import { AdminOrganizationEditDialog } from "@/components/admin/admin-organization-edit-dialog"
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
import { Button } from "@/components/ui/button"
import {
  DropdownMenu,
  DropdownMenuContent,
  DropdownMenuItem,
  DropdownMenuSeparator,
  DropdownMenuTrigger,
} from "@/components/ui/dropdown-menu"
import { useAdminOrganizations, useAdminOrgTiers } from "@/hooks/use-admin"

export function AdminOrganizationsTable() {
  const [editOrgId, setEditOrgId] = useState<string | null>(null)
  const [tierOrgId, setTierOrgId] = useState<string | null>(null)
  const [domainsOrgId, setDomainsOrgId] = useState<string | null>(null)
  const [registryOrgId, setRegistryOrgId] = useState<string | null>(null)
  const [selectedOrg, setSelectedOrg] = useState<OrgRead | null>(null)
  const { organizations, deleteOrganization } = useAdminOrganizations()
  const orgIds = organizations?.map((org) => org.id) ?? []
  const { orgTiersByOrgId, isLoading: orgTiersLoading } =
    useAdminOrgTiers(orgIds)

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
    <>
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
                <button
                  type="button"
                  className="text-xs font-medium hover:underline"
                  onClick={() => setEditOrgId(row.original.id)}
                >
                  {row.getValue<OrgRead["name"]>("name")}
                </button>
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
              id: "tier",
              header: ({ column }) => (
                <DataTableColumnHeader
                  className="text-xs"
                  column={column}
                  title="Tier"
                />
              ),
              cell: ({ row }) => {
                const orgTier = orgTiersByOrgId.get(row.original.id)
                const tier = orgTier?.tier

                if (tier) {
                  return (
                    <div className="flex items-center gap-2 text-xs">
                      <span className="font-medium">{tier.display_name}</span>
                    </div>
                  )
                }

                if (orgTiersLoading) {
                  return (
                    <span className="text-xs text-muted-foreground">
                      Loading...
                    </span>
                  )
                }

                return (
                  <span className="text-xs text-muted-foreground">Default</span>
                )
              },
              enableSorting: false,
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
                <div className="text-xs">
                  {row.getValue<OrgRead["is_active"]>("is_active")
                    ? "Active"
                    : "Inactive"}
                </div>
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
                      <DropdownMenuItem
                        onSelect={() => setEditOrgId(row.original.id)}
                      >
                        Edit organization
                      </DropdownMenuItem>
                      <DropdownMenuItem
                        onSelect={() => setTierOrgId(row.original.id)}
                      >
                        Manage tier
                      </DropdownMenuItem>
                      <DropdownMenuItem
                        onSelect={() => setDomainsOrgId(row.original.id)}
                      >
                        Manage domains
                      </DropdownMenuItem>
                      <DropdownMenuItem
                        onSelect={() => setRegistryOrgId(row.original.id)}
                      >
                        Manage registry
                      </DropdownMenuItem>
                      <DropdownMenuSeparator />
                      <DropdownMenuItem
                        onSelect={() =>
                          handleEnterOrganization(row.original.id)
                        }
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
      {editOrgId && (
        <AdminOrganizationEditDialog
          orgId={editOrgId}
          open
          onOpenChange={(isOpen) => {
            if (!isOpen) {
              setEditOrgId(null)
            }
          }}
        />
      )}
      {tierOrgId && (
        <AdminOrgTierDialog
          orgId={tierOrgId}
          open
          onOpenChange={(isOpen) => {
            if (!isOpen) {
              setTierOrgId(null)
            }
          }}
        />
      )}
      {domainsOrgId && (
        <AdminOrgDomainsDialog
          orgId={domainsOrgId}
          open
          onOpenChange={(isOpen) => {
            if (!isOpen) {
              setDomainsOrgId(null)
            }
          }}
        />
      )}
      {registryOrgId && (
        <AdminOrgRegistryDialog
          orgId={registryOrgId}
          open
          onOpenChange={(isOpen) => {
            if (!isOpen) {
              setRegistryOrgId(null)
            }
          }}
        />
      )}
    </>
  )
}

const defaultToolbarProps: DataTableToolbarProps<OrgRead> = {
  filterProps: {
    placeholder: "Filter organizations...",
    column: "name",
  },
}
