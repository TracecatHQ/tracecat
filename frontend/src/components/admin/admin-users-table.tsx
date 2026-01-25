"use client"

import { DotsHorizontalIcon } from "@radix-ui/react-icons"
import { useState } from "react"
import type { AdminUserRead } from "@/client"
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
import { toast } from "@/components/ui/use-toast"
import { useAdminUsers } from "@/hooks/use-admin"
import { useAuth } from "@/hooks/use-auth"
import { getRelativeTime } from "@/lib/event-history"

export function AdminUsersTable() {
  const [selectedUser, setSelectedUser] = useState<AdminUserRead | null>(null)
  const [actionType, setActionType] = useState<"promote" | "demote" | null>(
    null
  )
  const { user: currentUser } = useAuth()
  const { users, promoteToSuperuser, demoteFromSuperuser } = useAdminUsers()

  const superuserCount = users?.filter((u) => u.is_superuser).length ?? 0

  const handleConfirmAction = async () => {
    if (!selectedUser || !actionType) return

    try {
      if (actionType === "promote") {
        await promoteToSuperuser(selectedUser.id)
        toast({
          title: "User promoted",
          description: `${selectedUser.email} is now a superuser.`,
        })
      } else {
        await demoteFromSuperuser(selectedUser.id)
        toast({
          title: "User demoted",
          description: `${selectedUser.email} is no longer a superuser.`,
        })
      }
    } catch (error) {
      console.error(`Failed to ${actionType} user`, error)
      toast({
        title: "Action failed",
        description: `Failed to ${actionType} user. Please try again.`,
        variant: "destructive",
      })
    } finally {
      setSelectedUser(null)
      setActionType(null)
    }
  }

  return (
    <AlertDialog
      onOpenChange={(isOpen) => {
        if (!isOpen) {
          setSelectedUser(null)
          setActionType(null)
        }
      }}
    >
      <DataTable
        data={users ?? []}
        initialSortingState={[{ id: "email", desc: false }]}
        columns={[
          {
            accessorKey: "email",
            header: ({ column }) => (
              <DataTableColumnHeader
                className="text-xs"
                column={column}
                title="Email"
              />
            ),
            cell: ({ row }) => (
              <div className="text-xs font-medium">
                {row.getValue<AdminUserRead["email"]>("email")}
              </div>
            ),
            enableSorting: true,
            enableHiding: false,
          },
          {
            accessorKey: "first_name",
            header: ({ column }) => (
              <DataTableColumnHeader
                className="text-xs"
                column={column}
                title="First name"
              />
            ),
            cell: ({ row }) => (
              <div className="text-xs">
                {row.getValue<AdminUserRead["first_name"]>("first_name") || "-"}
              </div>
            ),
            enableSorting: true,
            enableHiding: true,
          },
          {
            accessorKey: "last_name",
            header: ({ column }) => (
              <DataTableColumnHeader
                className="text-xs"
                column={column}
                title="Last name"
              />
            ),
            cell: ({ row }) => (
              <div className="text-xs">
                {row.getValue<AdminUserRead["last_name"]>("last_name") || "-"}
              </div>
            ),
            enableSorting: true,
            enableHiding: true,
          },
          {
            accessorKey: "role",
            header: ({ column }) => (
              <DataTableColumnHeader
                className="text-xs"
                column={column}
                title="Role"
              />
            ),
            cell: ({ row }) => (
              <div className="text-xs capitalize">
                {row.getValue<AdminUserRead["role"]>("role")}
              </div>
            ),
            enableSorting: true,
            enableHiding: false,
          },
          {
            accessorKey: "is_superuser",
            header: ({ column }) => (
              <DataTableColumnHeader
                className="text-xs"
                column={column}
                title="Superuser"
              />
            ),
            cell: ({ row }) => (
              <Badge
                variant={
                  row.getValue<AdminUserRead["is_superuser"]>("is_superuser")
                    ? "default"
                    : "secondary"
                }
                className={
                  row.getValue<AdminUserRead["is_superuser"]>("is_superuser")
                    ? "bg-amber-500 hover:bg-amber-600"
                    : ""
                }
              >
                {row.getValue<AdminUserRead["is_superuser"]>("is_superuser")
                  ? "Yes"
                  : "No"}
              </Badge>
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
                  row.getValue<AdminUserRead["is_active"]>("is_active")
                    ? "default"
                    : "secondary"
                }
              >
                {row.getValue<AdminUserRead["is_active"]>("is_active")
                  ? "Active"
                  : "Inactive"}
              </Badge>
            ),
            enableSorting: true,
            enableHiding: false,
          },
          {
            accessorKey: "last_login_at",
            header: ({ column }) => (
              <DataTableColumnHeader
                className="text-xs"
                column={column}
                title="Last login"
              />
            ),
            cell: ({ row }) => {
              const lastLoginAt =
                row.getValue<AdminUserRead["last_login_at"]>("last_login_at")
              if (!lastLoginAt) {
                return <div className="text-xs text-muted-foreground">-</div>
              }
              const date = new Date(lastLoginAt)
              const ago = getRelativeTime(date)
              return (
                <div className="text-xs text-muted-foreground">
                  <span>{date.toLocaleDateString()}</span>
                  <span className="ml-1">({ago})</span>
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
              const rowUser = row.original
              const isSelf = currentUser?.id === rowUser.id
              const isLastSuperuser =
                rowUser.is_superuser && superuserCount <= 1

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
                      onClick={() => navigator.clipboard.writeText(rowUser.id)}
                    >
                      Copy user ID
                    </DropdownMenuItem>
                    <DropdownMenuSeparator />
                    {rowUser.is_superuser ? (
                      <AlertDialogTrigger asChild>
                        <DropdownMenuItem
                          className="text-rose-500 focus:text-rose-600"
                          disabled={isSelf || isLastSuperuser}
                          onClick={() => {
                            setSelectedUser(rowUser)
                            setActionType("demote")
                          }}
                        >
                          {isLastSuperuser
                            ? "Cannot demote last superuser"
                            : isSelf
                              ? "Cannot demote yourself"
                              : "Demote from superuser"}
                        </DropdownMenuItem>
                      </AlertDialogTrigger>
                    ) : (
                      <AlertDialogTrigger asChild>
                        <DropdownMenuItem
                          onClick={() => {
                            setSelectedUser(rowUser)
                            setActionType("promote")
                          }}
                        >
                          Promote to superuser
                        </DropdownMenuItem>
                      </AlertDialogTrigger>
                    )}
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
          <AlertDialogTitle>
            {actionType === "promote"
              ? "Promote to superuser"
              : "Demote from superuser"}
          </AlertDialogTitle>
          <AlertDialogDescription>
            {actionType === "promote" ? (
              <>
                Are you sure you want to promote {selectedUser?.email} to
                superuser? They will have full access to all admin functions.
              </>
            ) : (
              <>
                Are you sure you want to demote {selectedUser?.email} from
                superuser? They will lose access to admin functions.
              </>
            )}
          </AlertDialogDescription>
        </AlertDialogHeader>
        <AlertDialogFooter>
          <AlertDialogCancel>Cancel</AlertDialogCancel>
          <AlertDialogAction
            variant={actionType === "demote" ? "destructive" : "default"}
            onClick={handleConfirmAction}
          >
            {actionType === "promote" ? "Promote" : "Demote"}
          </AlertDialogAction>
        </AlertDialogFooter>
      </AlertDialogContent>
    </AlertDialog>
  )
}

const defaultToolbarProps: DataTableToolbarProps<AdminUserRead> = {
  filterProps: {
    placeholder: "Filter users by email...",
    column: "email",
  },
}
