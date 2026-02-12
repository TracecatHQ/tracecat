"use client"

import {
  CheckIcon,
  Cross2Icon,
  DotsHorizontalIcon,
} from "@radix-ui/react-icons"
import { useState } from "react"
import type { TierRead } from "@/client"
import { AdminTierEditDialog } from "@/components/admin/admin-tier-edit-dialog"
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
import { toast } from "@/components/ui/use-toast"
import { useAdminTiers } from "@/hooks/use-admin"
import { TIER_ENTITLEMENTS } from "@/lib/tier-entitlements"

export function AdminTiersTable() {
  const [editTierId, setEditTierId] = useState<string | null>(null)
  const [selectedTier, setSelectedTier] = useState<TierRead | null>(null)
  const { tiers, deleteTier } = useAdminTiers()

  const handleDeleteTier = async () => {
    if (selectedTier) {
      try {
        await deleteTier(selectedTier.id)
        toast({
          title: "Tier deleted",
          description: `${selectedTier.display_name} has been deleted.`,
        })
      } catch (error) {
        console.error("Failed to delete tier", error)
        toast({
          title: "Failed to delete tier",
          description:
            "The tier may have organizations assigned to it. Remove all assignments first.",
          variant: "destructive",
        })
      } finally {
        setSelectedTier(null)
      }
    }
  }

  return (
    <>
      <AlertDialog
        onOpenChange={(isOpen) => {
          if (!isOpen) {
            setSelectedTier(null)
          }
        }}
      >
        <DataTable
          data={tiers ?? []}
          columns={[
            {
              accessorKey: "display_name",
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
                  onClick={() => setEditTierId(row.original.id)}
                >
                  {row.getValue<TierRead["display_name"]>("display_name")}
                </button>
              ),
              enableSorting: true,
              enableHiding: false,
            },
            {
              accessorKey: "is_default",
              header: ({ column }) => (
                <DataTableColumnHeader
                  className="text-xs"
                  column={column}
                  title="Default"
                />
              ),
              cell: ({ row }) =>
                row.getValue<TierRead["is_default"]>("is_default") ? (
                  <div className="flex items-center text-xs font-medium">
                    <CheckIcon aria-label="Default tier" className="size-4" />
                  </div>
                ) : (
                  <div className="text-xs text-muted-foreground">-</div>
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
              cell: ({ row }) => {
                const isActive =
                  row.getValue<TierRead["is_active"]>("is_active")
                return (
                  <div className="flex items-center text-xs">
                    {isActive ? (
                      <CheckIcon aria-label="Active tier" className="size-4" />
                    ) : (
                      <Cross2Icon
                        aria-label="Inactive tier"
                        className="size-4 text-muted-foreground"
                      />
                    )}
                  </div>
                )
              },
              enableSorting: true,
              enableHiding: false,
            },
            {
              id: "entitlements",
              accessorFn: (row) =>
                getEnabledEntitlements(row)
                  .map((item) => item.label)
                  .join(", "),
              header: ({ column }) => (
                <DataTableColumnHeader
                  className="text-xs"
                  column={column}
                  title="Entitlements"
                />
              ),
              cell: ({ row }) => {
                const enabledEntitlements = getEnabledEntitlements(row.original)
                return (
                  <div className="text-xs text-muted-foreground">
                    {enabledEntitlements.length > 0 ? (
                      <div className="flex flex-col gap-1">
                        {enabledEntitlements.map((item) => (
                          <span key={item.key}>{item.label}</span>
                        ))}
                      </div>
                    ) : (
                      "None"
                    )}
                  </div>
                )
              },
              enableSorting: true,
              enableHiding: false,
            },
            {
              accessorKey: "max_concurrent_workflows",
              header: ({ column }) => (
                <DataTableColumnHeader
                  className="text-xs"
                  column={column}
                  title="Max workflows"
                />
              ),
              cell: ({ row }) => (
                <div className="text-xs text-muted-foreground">
                  {row.getValue<TierRead["max_concurrent_workflows"]>(
                    "max_concurrent_workflows"
                  ) ?? "Unlimited"}
                </div>
              ),
              enableSorting: true,
              enableHiding: true,
            },
            {
              accessorKey: "max_action_executions_per_workflow",
              header: ({ column }) => (
                <DataTableColumnHeader
                  className="text-xs"
                  column={column}
                  title="Max actions/workflow"
                />
              ),
              cell: ({ row }) => (
                <div className="text-xs text-muted-foreground">
                  {row.getValue<TierRead["max_action_executions_per_workflow"]>(
                    "max_action_executions_per_workflow"
                  ) ?? "Unlimited"}
                </div>
              ),
              enableSorting: true,
              enableHiding: true,
            },
            {
              accessorKey: "api_rate_limit",
              header: ({ column }) => (
                <DataTableColumnHeader
                  className="text-xs"
                  column={column}
                  title="API rate limit"
                />
              ),
              cell: ({ row }) => (
                <div className="text-xs text-muted-foreground">
                  {row.getValue<TierRead["api_rate_limit"]>("api_rate_limit") ??
                    "Unlimited"}
                </div>
              ),
              enableSorting: true,
              enableHiding: true,
            },
            {
              id: "actions",
              enableHiding: false,
              cell: ({ row }) => {
                const tier = row.original

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
                        onClick={() => navigator.clipboard.writeText(tier.id)}
                      >
                        Copy ID
                      </DropdownMenuItem>
                      <DropdownMenuItem onSelect={() => setEditTierId(tier.id)}>
                        Edit tier
                      </DropdownMenuItem>
                      <DropdownMenuSeparator />
                      <AlertDialogTrigger asChild>
                        <DropdownMenuItem
                          className="text-rose-500 focus:text-rose-600"
                          disabled={tier.is_default}
                          onClick={() => setSelectedTier(tier)}
                        >
                          {tier.is_default
                            ? "Cannot delete default tier"
                            : "Delete tier"}
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
            <AlertDialogTitle>Delete tier</AlertDialogTitle>
            <AlertDialogDescription>
              Are you sure you want to delete the tier &quot;
              {selectedTier?.display_name}&quot;? This action cannot be undone.
              Organizations assigned to this tier will need to be reassigned.
            </AlertDialogDescription>
          </AlertDialogHeader>
          <AlertDialogFooter>
            <AlertDialogCancel>Cancel</AlertDialogCancel>
            <AlertDialogAction variant="destructive" onClick={handleDeleteTier}>
              Delete
            </AlertDialogAction>
          </AlertDialogFooter>
        </AlertDialogContent>
      </AlertDialog>
      {editTierId && (
        <AdminTierEditDialog
          tierId={editTierId}
          open
          onOpenChange={(isOpen) => {
            if (!isOpen) {
              setEditTierId(null)
            }
          }}
        />
      )}
    </>
  )
}

const defaultToolbarProps: DataTableToolbarProps<TierRead> = {
  filterProps: {
    placeholder: "Filter tiers...",
    column: "display_name",
  },
}

function getEnabledEntitlements(tier: TierRead) {
  return TIER_ENTITLEMENTS.filter(
    (entitlement) => tier.entitlements[entitlement.key] === true
  )
}
