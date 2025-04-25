"use client"

import { useState } from "react"
import { OrgMemberRead, UserRole } from "@/client"
import { useAuth } from "@/providers/auth"
import { DotsHorizontalIcon } from "@radix-ui/react-icons"

import { userIsPrivileged } from "@/lib/auth"
import { getRelativeTime } from "@/lib/event-history"
import { useOrgMembers } from "@/lib/hooks"
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
  Dialog,
  DialogContent,
  DialogDescription,
  DialogFooter,
  DialogHeader,
  DialogTitle,
  DialogTrigger,
} from "@/components/ui/dialog"
import {
  DropdownMenu,
  DropdownMenuContent,
  DropdownMenuGroup,
  DropdownMenuItem,
  DropdownMenuTrigger,
} from "@/components/ui/dropdown-menu"
import {
  Select,
  SelectContent,
  SelectItem,
  SelectTrigger,
  SelectValue,
} from "@/components/ui/select"
import { toast } from "@/components/ui/use-toast"
import {
  DataTable,
  DataTableColumnHeader,
  type DataTableToolbarProps,
} from "@/components/data-table"

export function OrgMembersTable() {
  const [selectedMember, setSelectedMember] = useState<OrgMemberRead | null>(
    null
  )
  const [isChangeRoleOpen, setIsChangeRoleOpen] = useState(false)
  const { user } = useAuth()
  const { orgMembers, updateOrgMember, deleteOrgMember } = useOrgMembers()

  const handleChangeRole = async (role: UserRole) => {
    try {
      if (selectedMember) {
        if (selectedMember.role === role) {
          toast({
            title: "Update skipped",
            description: `User ${selectedMember.email} is already a ${role} member`,
          })
          return
        }
        console.log("Changing role", selectedMember, role)
        await updateOrgMember({
          userId: selectedMember.user_id,
          requestBody: { role },
        })
      }
    } catch (error) {
      console.error("Failed to change role", error)
    } finally {
      setIsChangeRoleOpen(false)
      setSelectedMember(null)
    }
  }

  const handleRemoveMember = async () => {
    if (selectedMember) {
      console.log("Removing member", selectedMember)
      try {
        await deleteOrgMember({
          userId: selectedMember.user_id,
        })
      } catch (error) {
        console.error("Failed to remove member", error)
      } finally {
        setSelectedMember(null)
      }
    }
  }
  // Since this is the org members table, should only superusers be able to change roles?
  const privileged = userIsPrivileged(user)
  return (
    <Dialog open={isChangeRoleOpen} onOpenChange={setIsChangeRoleOpen}>
      <AlertDialog
        onOpenChange={(isOpen) => {
          if (!isOpen) {
            setSelectedMember(null)
          }
        }}
      >
        <DataTable
          data={orgMembers}
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
                <div className="text-xs">
                  {row.getValue<OrgMemberRead["email"]>("email")}
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
                  {row.getValue<OrgMemberRead["first_name"]>("first_name") ||
                    "-"}
                </div>
              ),
              enableSorting: true,
              enableHiding: false,
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
                  {row.getValue<OrgMemberRead["last_name"]>("last_name") || "-"}
                </div>
              ),
              enableSorting: true,
              enableHiding: false,
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
                  {row.getValue<OrgMemberRead["role"]>("role")}
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
                <div className="text-xs capitalize">
                  {row.getValue<OrgMemberRead["is_active"]>("is_active")
                    ? "Yes"
                    : "No"}
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
                <div className="text-xs capitalize">
                  {row.getValue<OrgMemberRead["is_superuser"]>("is_superuser")
                    ? "Yes"
                    : "No"}
                </div>
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
                  row.getValue<OrgMemberRead["last_login_at"]>("last_login_at")
                if (!lastLoginAt) {
                  return <div className="text-xs">-</div>
                }
                const date = new Date(lastLoginAt)
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
                return (
                  <DropdownMenu>
                    <DropdownMenuTrigger asChild>
                      <Button variant="ghost" className="size-8 p-0">
                        <span className="sr-only">Open menu</span>
                        <DotsHorizontalIcon className="size-4" />
                      </Button>
                    </DropdownMenuTrigger>
                    <DropdownMenuContent>
                      <DropdownMenuItem
                        onClick={() =>
                          navigator.clipboard.writeText(row.original.user_id)
                        }
                      >
                        Copy user ID
                      </DropdownMenuItem>

                      {privileged && (
                        <DropdownMenuGroup>
                          <DialogTrigger asChild>
                            <DropdownMenuItem
                              onClick={() => {
                                setSelectedMember(row.original)
                                setIsChangeRoleOpen(true)
                              }}
                            >
                              Change role
                            </DropdownMenuItem>
                          </DialogTrigger>

                          <AlertDialogTrigger asChild>
                            <DropdownMenuItem
                              className="text-rose-500 focus:text-rose-600"
                              onClick={() => {
                                setSelectedMember(row.original)
                                console.debug("Selected user", row.original)
                              }}
                            >
                              Remove from organization
                            </DropdownMenuItem>
                          </AlertDialogTrigger>
                        </DropdownMenuGroup>
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
            <AlertDialogTitle>Remove user</AlertDialogTitle>
            <AlertDialogDescription>
              Are you sure you want to remove this user from the organization?
              This action cannot be undone.
            </AlertDialogDescription>
          </AlertDialogHeader>
          <AlertDialogFooter>
            <AlertDialogCancel>Cancel</AlertDialogCancel>
            <AlertDialogAction
              variant="destructive"
              onClick={handleRemoveMember}
            >
              Confirm
            </AlertDialogAction>
          </AlertDialogFooter>
        </AlertDialogContent>
      </AlertDialog>
      <ChangeUserRoleDialog
        selectedUser={selectedMember}
        setOpen={setIsChangeRoleOpen}
        onConfirm={handleChangeRole}
      />
    </Dialog>
  )
}

function ChangeUserRoleDialog({
  selectedUser,
  setOpen,
  onConfirm,
}: {
  selectedUser: OrgMemberRead | null
  setOpen: (open: boolean) => void
  onConfirm: (role: UserRole) => void
}) {
  const [newRole, setNewRole] = useState<UserRole>("basic")
  return (
    <DialogContent>
      <DialogHeader>
        <DialogTitle>Change User Role</DialogTitle>
        <DialogDescription>
          Select a new role for {selectedUser?.email}
        </DialogDescription>
      </DialogHeader>
      <Select
        value={newRole}
        onValueChange={(value) => setNewRole(value as UserRole)}
      >
        <SelectTrigger>
          <SelectValue placeholder="Select a role" />
        </SelectTrigger>
        <SelectContent>
          <SelectItem value="admin">Admin</SelectItem>
          <SelectItem value="basic">Basic</SelectItem>
        </SelectContent>
      </Select>
      <DialogFooter>
        <Button variant="outline" onClick={() => setOpen(false)}>
          Cancel
        </Button>
        <Button onClick={() => onConfirm(newRole)}>Change Role</Button>
      </DialogFooter>
    </DialogContent>
  )
}

const defaultToolbarProps: DataTableToolbarProps<OrgMemberRead> = {
  filterProps: {
    placeholder: "Filter users by email...",
    column: "email",
  },
}
