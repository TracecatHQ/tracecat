"use client"

import { zodResolver } from "@hookform/resolvers/zod"
import { DotsHorizontalIcon, PlusIcon } from "@radix-ui/react-icons"
import { useState } from "react"
import { useForm } from "react-hook-form"
import { z } from "zod"
import {
  type OrgInvitationRead,
  type OrgRole,
  organizationGetInvitationToken,
} from "@/client"
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
import {
  Select,
  SelectContent,
  SelectItem,
  SelectTrigger,
  SelectValue,
} from "@/components/ui/select"
import { toast } from "@/components/ui/use-toast"
import { useOrgMembership } from "@/hooks/use-org-membership"
import { getRelativeTime } from "@/lib/event-history"
import { useOrgInvitations } from "@/lib/hooks"

const invitationFormSchema = z.object({
  email: z.string().email("Invalid email address"),
  role: z.enum(["member", "admin", "owner"]),
})

type InvitationFormValues = z.infer<typeof invitationFormSchema>

export function OrgInvitationsTable() {
  const [selectedInvitation, setSelectedInvitation] =
    useState<OrgInvitationRead | null>(null)
  const [isCreateDialogOpen, setIsCreateDialogOpen] = useState(false)
  const { canAdministerOrg } = useOrgMembership()
  const { invitations, createInvitation, createPending, revokeInvitation } =
    useOrgInvitations()

  const form = useForm<InvitationFormValues>({
    resolver: zodResolver(invitationFormSchema),
    defaultValues: {
      email: "",
      role: "member",
    },
  })

  const handleCreateInvitation = async (values: InvitationFormValues) => {
    try {
      await createInvitation({
        email: values.email,
        role: values.role as OrgRole,
      })
      form.reset()
      setIsCreateDialogOpen(false)
    } catch {
      // Error is handled in the hook
    }
  }

  const handleRevokeInvitation = async () => {
    if (!selectedInvitation) return
    try {
      await revokeInvitation(selectedInvitation.id)
    } catch {
      // Error is handled in the hook
    } finally {
      setSelectedInvitation(null)
    }
  }

  const getStatusBadgeVariant = (
    status: OrgInvitationRead["status"]
  ): "default" | "secondary" | "destructive" | "outline" => {
    switch (status) {
      case "pending":
        return "outline"
      case "accepted":
        return "default"
      case "revoked":
        return "destructive"
      default:
        return "secondary"
    }
  }

  return (
    <div className="space-y-4">
      {canAdministerOrg && (
        <div className="flex justify-end">
          <Dialog
            open={isCreateDialogOpen}
            onOpenChange={setIsCreateDialogOpen}
          >
            <DialogTrigger asChild>
              <Button size="sm">
                <PlusIcon className="mr-2 size-4" />
                Invite member
              </Button>
            </DialogTrigger>
            <DialogContent>
              <DialogHeader>
                <DialogTitle>Invite member</DialogTitle>
                <DialogDescription>
                  Send an invitation to join this organization.
                </DialogDescription>
              </DialogHeader>
              <Form {...form}>
                <form
                  onSubmit={form.handleSubmit(handleCreateInvitation)}
                  className="space-y-4"
                >
                  <FormField
                    control={form.control}
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
                        <FormDescription>
                          The email address of the person to invite.
                        </FormDescription>
                        <FormMessage />
                      </FormItem>
                    )}
                  />
                  <FormField
                    control={form.control}
                    name="role"
                    render={({ field }) => (
                      <FormItem>
                        <FormLabel>Role</FormLabel>
                        <Select
                          onValueChange={field.onChange}
                          defaultValue={field.value}
                        >
                          <FormControl>
                            <SelectTrigger>
                              <SelectValue placeholder="Select a role" />
                            </SelectTrigger>
                          </FormControl>
                          <SelectContent>
                            <SelectItem value="member">Member</SelectItem>
                            <SelectItem value="admin">Admin</SelectItem>
                            <SelectItem value="owner">Owner</SelectItem>
                          </SelectContent>
                        </Select>
                        <FormDescription>
                          The role to assign when the invitation is accepted.
                        </FormDescription>
                        <FormMessage />
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
                      {createPending ? "Sending..." : "Send invitation"}
                    </Button>
                  </DialogFooter>
                </form>
              </Form>
            </DialogContent>
          </Dialog>
        </div>
      )}
      <AlertDialog
        onOpenChange={(isOpen) => {
          if (!isOpen) {
            setSelectedInvitation(null)
          }
        }}
      >
        <DataTable
          data={invitations ?? []}
          initialSortingState={[{ id: "created_at", desc: true }]}
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
                  {row.getValue<OrgInvitationRead["email"]>("email")}
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
                  {row.getValue<OrgInvitationRead["role"]>("role")}
                </div>
              ),
              enableSorting: true,
              enableHiding: false,
            },
            {
              accessorKey: "status",
              header: ({ column }) => (
                <DataTableColumnHeader
                  className="text-xs"
                  column={column}
                  title="Status"
                />
              ),
              cell: ({ row }) => {
                const status =
                  row.getValue<OrgInvitationRead["status"]>("status")
                return (
                  <Badge variant={getStatusBadgeVariant(status)}>
                    {status.charAt(0).toUpperCase() + status.slice(1)}
                  </Badge>
                )
              },
              enableSorting: true,
              enableHiding: false,
            },
            {
              accessorKey: "expires_at",
              header: ({ column }) => (
                <DataTableColumnHeader
                  className="text-xs"
                  column={column}
                  title="Expires"
                />
              ),
              cell: ({ row }) => {
                const expiresAt =
                  row.getValue<OrgInvitationRead["expires_at"]>("expires_at")
                const date = new Date(expiresAt)
                const isExpired = date < new Date()
                const ago = getRelativeTime(date)
                return (
                  <div
                    className={`text-xs ${isExpired ? "text-destructive" : "text-muted-foreground"}`}
                  >
                    <span>{date.toLocaleDateString()}</span>
                    <span className="ml-1">({ago})</span>
                  </div>
                )
              },
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
                  row.getValue<OrgInvitationRead["created_at"]>("created_at")
                const date = new Date(createdAt)
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
                const invitation = row.original
                const isPending = invitation.status === "pending"

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
                          navigator.clipboard.writeText(invitation.id)
                        }
                      >
                        Copy invitation ID
                      </DropdownMenuItem>
                      {canAdministerOrg && isPending && (
                        <DropdownMenuItem
                          onSelect={async () => {
                            try {
                              const { token } =
                                await organizationGetInvitationToken({
                                  invitationId: invitation.id,
                                })
                              const url = `${window.location.origin}/invitations/accept?token=${token}`
                              await navigator.clipboard.writeText(url)
                              toast({
                                title: "Copied",
                                description:
                                  "Invitation link copied to clipboard",
                              })
                            } catch {
                              toast({
                                title: "Error",
                                description: "Failed to copy invitation link",
                                variant: "destructive",
                              })
                            }
                          }}
                        >
                          Copy invitation link
                        </DropdownMenuItem>
                      )}
                      {canAdministerOrg && (
                        <>
                          <DropdownMenuSeparator />
                          {isPending ? (
                            <AlertDialogTrigger asChild>
                              <DropdownMenuItem
                                className="text-rose-500 focus:text-rose-600"
                                onSelect={() =>
                                  setSelectedInvitation(invitation)
                                }
                              >
                                Revoke invitation
                              </DropdownMenuItem>
                            </AlertDialogTrigger>
                          ) : (
                            <DropdownMenuItem disabled>
                              {invitation.status === "accepted"
                                ? "Already accepted"
                                : "Already revoked"}
                            </DropdownMenuItem>
                          )}
                        </>
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
            <AlertDialogTitle>Revoke invitation</AlertDialogTitle>
            <AlertDialogDescription>
              Are you sure you want to revoke the invitation for{" "}
              {selectedInvitation?.email}? They will no longer be able to join
              this organization with this invitation.
            </AlertDialogDescription>
          </AlertDialogHeader>
          <AlertDialogFooter>
            <AlertDialogCancel>Cancel</AlertDialogCancel>
            <AlertDialogAction
              variant="destructive"
              onClick={handleRevokeInvitation}
            >
              Revoke
            </AlertDialogAction>
          </AlertDialogFooter>
        </AlertDialogContent>
      </AlertDialog>
    </div>
  )
}

const defaultToolbarProps: DataTableToolbarProps<OrgInvitationRead> = {
  filterProps: {
    placeholder: "Filter by email...",
    column: "email",
  },
}
