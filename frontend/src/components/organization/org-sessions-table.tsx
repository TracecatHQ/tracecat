"use client"

import { DotsHorizontalIcon } from "@radix-ui/react-icons"
import { useCallback, useState } from "react"
import type { SessionRead } from "@/client"
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
import { Dialog } from "@/components/ui/dialog"
import {
  DropdownMenu,
  DropdownMenuContent,
  DropdownMenuGroup,
  DropdownMenuItem,
  DropdownMenuTrigger,
} from "@/components/ui/dropdown-menu"
import { getRelativeTime } from "@/lib/event-history"
import { useSessions } from "@/lib/hooks"
import { useAuth } from "@/providers/auth"

export function OrgSessionsTable() {
  const [selectedSession, setSelectedSession] = useState<SessionRead | null>(
    null
  )
  const [isChangeRoleOpen, setIsChangeRoleOpen] = useState(false)
  const { user } = useAuth()
  const { sessions, deleteSession } = useSessions()

  const handleRevokeSession = useCallback(async () => {
    if (!selectedSession) {
      return
    }
    try {
      await deleteSession({ sessionId: selectedSession.id })
    } catch (error) {
      console.error("Error deleting session", error)
    }
  }, [selectedSession, deleteSession])

  // Since this is the org members table, should only superusers be able to change roles?
  return (
    <Dialog open={isChangeRoleOpen} onOpenChange={setIsChangeRoleOpen}>
      <AlertDialog
        onOpenChange={(isOpen) => {
          if (!isOpen) {
            setSelectedSession(null)
          }
        }}
      >
        <DataTable
          data={sessions}
          initialSortingState={[{ id: "created_at", desc: true }]}
          columns={[
            {
              accessorKey: "user_email",
              header: ({ column }) => (
                <DataTableColumnHeader
                  className="text-xs"
                  column={column}
                  title="Email"
                />
              ),
              cell: ({ row }) => (
                <div className="text-xs">
                  {row.getValue<SessionRead["user_email"]>("user_email")}
                </div>
              ),
              enableSorting: true,
              enableHiding: false,
            },
            {
              accessorKey: "created_at",
              header: ({ column }) => (
                <DataTableColumnHeader column={column} title="Created at" />
              ),
              cell: ({ row }) => {
                const createdAt =
                  row.getValue<SessionRead["created_at"]>("created_at")
                const date = new Date(createdAt)
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
                      {user?.isPrivileged() && (
                        <DropdownMenuGroup>
                          <AlertDialogTrigger asChild>
                            <DropdownMenuItem
                              className="text-rose-500 focus:text-rose-600"
                              onClick={() => setSelectedSession(row.original)}
                            >
                              Revoke session
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
            <AlertDialogTitle>Revoke session</AlertDialogTitle>
            <AlertDialogDescription>
              Are you sure you want to revoke the session for{" "}
              <span className="font-semibold">
                {selectedSession?.user_email}
              </span>{" "}
              created at{" "}
              <span className="font-semibold">
                {selectedSession &&
                  new Date(selectedSession.created_at).toLocaleString()}
              </span>
              . This action cannot be undone.
            </AlertDialogDescription>
          </AlertDialogHeader>
          <AlertDialogFooter>
            <AlertDialogCancel>Cancel</AlertDialogCancel>
            <AlertDialogAction
              variant="destructive"
              onClick={handleRevokeSession}
            >
              Confirm
            </AlertDialogAction>
          </AlertDialogFooter>
        </AlertDialogContent>
      </AlertDialog>
    </Dialog>
  )
}

const defaultToolbarProps: DataTableToolbarProps<SessionRead> = {
  filterProps: {
    placeholder: "Filter sessions by user email...",
    column: "user_email",
  },
}
