"use client"

import { zodResolver } from "@hookform/resolvers/zod"
import { DotsHorizontalIcon } from "@radix-ui/react-icons"
import { PlusIcon, Trash2Icon } from "lucide-react"
import { useState } from "react"
import { useForm } from "react-hook-form"
import { z } from "zod"
import { type AdminUserRead, ApiError } from "@/client"
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
import { Checkbox } from "@/components/ui/checkbox"
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
  DropdownMenuItem,
  DropdownMenuSeparator,
  DropdownMenuTrigger,
} from "@/components/ui/dropdown-menu"
import {
  Form,
  FormControl,
  FormDescription,
  FormField,
  FormItem,
  FormLabel,
  FormMessage,
} from "@/components/ui/form"
import { Input } from "@/components/ui/input"
import { toast } from "@/components/ui/use-toast"
import { useAdminUsers } from "@/hooks/use-admin"
import { useAuth } from "@/hooks/use-auth"
import { getRelativeTime } from "@/lib/event-history"

const createUserFormSchema = z.object({
  email: z.string().email("Invalid email address"),
  password: z.string().min(12, "Password must be at least 12 characters long"),
  firstName: z.string().max(255, "First name is too long"),
  lastName: z.string().max(255, "Last name is too long"),
  isSuperuser: z.boolean(),
})

type CreateUserFormValues = z.infer<typeof createUserFormSchema>

export function AdminUsersTable() {
  const [selectedUser, setSelectedUser] = useState<AdminUserRead | null>(null)
  const [isCreateDialogOpen, setIsCreateDialogOpen] = useState(false)
  const [actionType, setActionType] = useState<
    "promote" | "demote" | "delete" | null
  >(null)
  const { user: currentUser } = useAuth()
  const {
    users,
    createUser,
    createPending,
    promoteToSuperuser,
    promotePending,
    demoteFromSuperuser,
    demotePending,
    deleteUser,
    deletePending,
  } = useAdminUsers()
  const createUserForm = useForm<CreateUserFormValues>({
    resolver: zodResolver(createUserFormSchema),
    defaultValues: {
      email: "",
      password: "",
      firstName: "",
      lastName: "",
      isSuperuser: false,
    },
  })

  const superuserCount = users?.filter((u) => u.is_superuser).length ?? 0

  const handleCreateUser = async (values: CreateUserFormValues) => {
    try {
      await createUser({
        email: values.email,
        password: values.password,
        first_name: values.firstName.trim() || null,
        last_name: values.lastName.trim() || null,
        is_superuser: values.isSuperuser,
      })
      toast({
        title: "User created",
        description: `${values.email} was created successfully.`,
      })
      createUserForm.reset()
      setIsCreateDialogOpen(false)
    } catch (error) {
      let description = "Failed to create user. Please try again."
      if (error instanceof ApiError) {
        const body = error.body as { detail?: unknown }
        if (typeof body.detail === "string") {
          description = body.detail
        }
      }
      console.error("Failed to create user", error)
      toast({
        title: "Create user failed",
        description,
        variant: "destructive",
      })
    }
  }

  const handleConfirmAction = async () => {
    if (!selectedUser || !actionType) return

    try {
      if (actionType === "promote") {
        await promoteToSuperuser(selectedUser.id)
        toast({
          title: "User promoted",
          description: `${selectedUser.email} is now a superuser.`,
        })
      } else if (actionType === "demote") {
        await demoteFromSuperuser(selectedUser.id)
        toast({
          title: "User demoted",
          description: `${selectedUser.email} is no longer a superuser.`,
        })
      } else {
        await deleteUser(selectedUser.id)
        toast({
          title: "User deleted",
          description: `${selectedUser.email} was deleted.`,
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

  let dialogTitle = "Delete user"
  let actionButtonLabel = "Delete"
  let dialogDescription = (
    <>
      Are you sure you want to delete {selectedUser?.email}? This action cannot
      be undone.
    </>
  )

  if (actionType === "promote") {
    dialogTitle = "Promote to superuser"
    actionButtonLabel = "Promote"
    dialogDescription = (
      <>
        Are you sure you want to promote {selectedUser?.email} to superuser?
        They will have full access to all admin functions.
      </>
    )
  } else if (actionType === "demote") {
    dialogTitle = "Demote from superuser"
    actionButtonLabel = "Demote"
    dialogDescription = (
      <>
        Are you sure you want to demote {selectedUser?.email} from superuser?
        They will lose access to admin functions.
      </>
    )
  }

  return (
    <div className="space-y-4">
      <div className="flex justify-end">
        <Dialog
          open={isCreateDialogOpen}
          onOpenChange={(isOpen) => {
            setIsCreateDialogOpen(isOpen)
            if (!isOpen) {
              createUserForm.reset()
            }
          }}
        >
          <DialogTrigger asChild>
            <Button size="sm">
              <PlusIcon className="mr-2 size-4" />
              Create user
            </Button>
          </DialogTrigger>
          <DialogContent>
            <DialogHeader>
              <DialogTitle>Create user</DialogTitle>
              <DialogDescription>
                Create a platform user without adding them to an organization.
              </DialogDescription>
            </DialogHeader>
            <Form {...createUserForm}>
              <form
                onSubmit={createUserForm.handleSubmit(handleCreateUser)}
                className="space-y-4"
              >
                <FormField
                  control={createUserForm.control}
                  name="email"
                  render={({ field }) => (
                    <FormItem>
                      <FormLabel>Email</FormLabel>
                      <FormControl>
                        <Input
                          placeholder="user@example.com"
                          type="email"
                          {...field}
                        />
                      </FormControl>
                      <FormMessage />
                    </FormItem>
                  )}
                />
                <FormField
                  control={createUserForm.control}
                  name="password"
                  render={({ field }) => (
                    <FormItem>
                      <FormLabel>Password</FormLabel>
                      <FormControl>
                        <Input
                          type="password"
                          placeholder="••••••••••••••••"
                          {...field}
                        />
                      </FormControl>
                      <FormDescription>
                        Minimum 12 characters. This is the initial password.
                      </FormDescription>
                      <FormMessage />
                    </FormItem>
                  )}
                />
                <div className="grid gap-4 sm:grid-cols-2">
                  <FormField
                    control={createUserForm.control}
                    name="firstName"
                    render={({ field }) => (
                      <FormItem>
                        <FormLabel>First name</FormLabel>
                        <FormControl>
                          <Input placeholder="Jane" {...field} />
                        </FormControl>
                        <FormMessage />
                      </FormItem>
                    )}
                  />
                  <FormField
                    control={createUserForm.control}
                    name="lastName"
                    render={({ field }) => (
                      <FormItem>
                        <FormLabel>Last name</FormLabel>
                        <FormControl>
                          <Input placeholder="Doe" {...field} />
                        </FormControl>
                        <FormMessage />
                      </FormItem>
                    )}
                  />
                </div>
                <FormField
                  control={createUserForm.control}
                  name="isSuperuser"
                  render={({ field }) => (
                    <FormItem className="flex flex-row items-start space-x-3 space-y-0 rounded-md border p-4">
                      <FormControl>
                        <Checkbox
                          checked={field.value}
                          onCheckedChange={field.onChange}
                        />
                      </FormControl>
                      <div className="space-y-1 leading-none">
                        <FormLabel>Grant superuser access</FormLabel>
                        <FormDescription>
                          Superusers can access all platform admin functions.
                        </FormDescription>
                      </div>
                    </FormItem>
                  )}
                />
                <DialogFooter>
                  <Button
                    type="button"
                    variant="outline"
                    onClick={() => setIsCreateDialogOpen(false)}
                  >
                    Cancel
                  </Button>
                  <Button type="submit" disabled={createPending}>
                    {createPending ? "Creating..." : "Create"}
                  </Button>
                </DialogFooter>
              </form>
            </Form>
          </DialogContent>
        </Dialog>
      </div>

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
                  {row.getValue<AdminUserRead["first_name"]>("first_name") ||
                    "-"}
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
                <div className="text-xs">
                  {row.getValue<AdminUserRead["is_superuser"]>("is_superuser")
                    ? "Yes"
                    : "No"}
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
                <div className="text-xs">
                  {row.getValue<AdminUserRead["is_active"]>("is_active")
                    ? "Active"
                    : "Inactive"}
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
                const cannotDeleteReason = isSelf
                  ? "Cannot delete yourself"
                  : isLastSuperuser
                    ? "Cannot delete last superuser"
                    : null

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
                        onSelect={() =>
                          navigator.clipboard.writeText(rowUser.id)
                        }
                      >
                        Copy user ID
                      </DropdownMenuItem>
                      <DropdownMenuSeparator />
                      {rowUser.is_superuser ? (
                        <AlertDialogTrigger asChild>
                          <DropdownMenuItem
                            className="text-rose-500 focus:text-rose-600"
                            disabled={isSelf || isLastSuperuser}
                            onSelect={() => {
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
                            onSelect={() => {
                              setSelectedUser(rowUser)
                              setActionType("promote")
                            }}
                          >
                            Promote to superuser
                          </DropdownMenuItem>
                        </AlertDialogTrigger>
                      )}
                      <AlertDialogTrigger asChild>
                        <DropdownMenuItem
                          className="text-rose-500 focus:text-rose-600"
                          disabled={cannotDeleteReason !== null}
                          onSelect={() => {
                            setSelectedUser(rowUser)
                            setActionType("delete")
                          }}
                        >
                          <Trash2Icon className="mr-2 size-4" />
                          {cannotDeleteReason ?? "Delete user"}
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
            <AlertDialogTitle>{dialogTitle}</AlertDialogTitle>
            <AlertDialogDescription>{dialogDescription}</AlertDialogDescription>
          </AlertDialogHeader>
          <AlertDialogFooter>
            <AlertDialogCancel>Cancel</AlertDialogCancel>
            <AlertDialogAction
              variant={actionType === "promote" ? "default" : "destructive"}
              disabled={promotePending || demotePending || deletePending}
              onClick={handleConfirmAction}
            >
              {actionButtonLabel}
            </AlertDialogAction>
          </AlertDialogFooter>
        </AlertDialogContent>
      </AlertDialog>
    </div>
  )
}

const defaultToolbarProps: DataTableToolbarProps<AdminUserRead> = {
  filterProps: {
    placeholder: "Filter users by email...",
    column: "email",
  },
}
