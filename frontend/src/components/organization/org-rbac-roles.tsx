"use client"

import { DotsHorizontalIcon } from "@radix-ui/react-icons"
import { PlusIcon } from "lucide-react"
import { useCallback, useState } from "react"
import type { RoleReadWithScopes, ScopeRead } from "@/client"
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
import { Input } from "@/components/ui/input"
import { Label } from "@/components/ui/label"
import { ScrollArea } from "@/components/ui/scroll-area"
import { useRbacRoles, useRbacScopes } from "@/lib/hooks"

export function OrgRbacRoles() {
  const [selectedRole, setSelectedRole] = useState<RoleReadWithScopes | null>(
    null
  )
  const [isCreateOpen, setIsCreateOpen] = useState(false)
  const [isEditOpen, setIsEditOpen] = useState(false)
  const {
    roles,
    isLoading,
    error,
    createRole,
    createRoleIsPending,
    updateRole,
    updateRoleIsPending,
    deleteRole,
    deleteRoleIsPending,
  } = useRbacRoles()

  const { scopes } = useRbacScopes({ includeSystem: true })

  const handleCreateRole = async (
    name: string,
    description: string,
    scopeIds: string[]
  ) => {
    await createRole({
      name,
      description: description || undefined,
      scope_ids: scopeIds,
    })
    setIsCreateOpen(false)
  }

  const handleUpdateRole = async (
    roleId: string,
    name: string,
    description: string,
    scopeIds: string[]
  ) => {
    await updateRole({
      roleId,
      name,
      description: description || undefined,
      scope_ids: scopeIds,
    })
    setIsEditOpen(false)
    setSelectedRole(null)
  }

  const handleDeleteRole = async () => {
    if (selectedRole) {
      await deleteRole(selectedRole.id)
      setSelectedRole(null)
    }
  }

  return (
    <Dialog
      open={isCreateOpen || isEditOpen}
      onOpenChange={(open) => {
        if (!open) {
          setIsCreateOpen(false)
          setIsEditOpen(false)
          setSelectedRole(null)
        }
      }}
    >
      <AlertDialog
        onOpenChange={(isOpen) => {
          if (!isOpen) {
            setSelectedRole(null)
          }
        }}
      >
        <div className="space-y-4">
          <div className="flex items-center justify-between">
            <p className="text-sm text-muted-foreground">
              Roles bundle scopes together for assignment to groups.
            </p>
            <DialogTrigger asChild>
              <Button size="sm" onClick={() => setIsCreateOpen(true)}>
                <PlusIcon className="mr-2 size-4" />
                Create role
              </Button>
            </DialogTrigger>
          </div>

          <DataTable
            data={roles}
            isLoading={isLoading}
            error={error as Error | null}
            emptyMessage="No roles found"
            initialSortingState={[{ id: "name", desc: false }]}
            columns={[
              {
                accessorKey: "name",
                header: ({ column }) => (
                  <DataTableColumnHeader
                    className="text-xs"
                    column={column}
                    title="Role"
                  />
                ),
                cell: ({ row }) => (
                  <div className="flex items-center gap-2">
                    <span className="text-sm font-medium">
                      {row.getValue<string>("name")}
                    </span>
                    {row.original.is_system && (
                      <span className="rounded-full bg-muted px-2 py-0.5 text-[10px] font-medium text-muted-foreground">
                        System
                      </span>
                    )}
                  </div>
                ),
                enableSorting: true,
                enableHiding: false,
              },
              {
                accessorKey: "description",
                header: ({ column }) => (
                  <DataTableColumnHeader
                    className="text-xs"
                    column={column}
                    title="Description"
                  />
                ),
                cell: ({ row }) => (
                  <span className="text-xs text-muted-foreground line-clamp-1">
                    {row.getValue<string>("description") || "-"}
                  </span>
                ),
                enableSorting: false,
                enableHiding: true,
              },
              {
                accessorKey: "scopes",
                header: ({ column }) => (
                  <DataTableColumnHeader
                    className="text-xs"
                    column={column}
                    title="Scopes"
                  />
                ),
                cell: ({ row }) => {
                  const roleScopes = row.original.scopes ?? []
                  return (
                    <div className="flex items-center gap-1">
                      <span className="text-xs text-muted-foreground">
                        {roleScopes.length} scope
                        {roleScopes.length !== 1 && "s"}
                      </span>
                    </div>
                  )
                },
                enableSorting: false,
                enableHiding: true,
              },
              {
                id: "actions",
                enableHiding: false,
                cell: ({ row }) => {
                  const role = row.original
                  const isSystem = role.is_system

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
                          onClick={() => navigator.clipboard.writeText(role.id)}
                        >
                          Copy role ID
                        </DropdownMenuItem>
                        {!isSystem && (
                          <>
                            <DropdownMenuSeparator />
                            <DialogTrigger asChild>
                              <DropdownMenuItem
                                onClick={() => {
                                  setSelectedRole(role)
                                  setIsEditOpen(true)
                                }}
                              >
                                Edit role
                              </DropdownMenuItem>
                            </DialogTrigger>
                            <AlertDialogTrigger asChild>
                              <DropdownMenuItem
                                className="text-rose-500 focus:text-rose-600"
                                onClick={() => setSelectedRole(role)}
                              >
                                Delete role
                              </DropdownMenuItem>
                            </AlertDialogTrigger>
                          </>
                        )}
                      </DropdownMenuContent>
                    </DropdownMenu>
                  )
                },
              },
            ]}
            toolbarProps={toolbarProps}
          />
        </div>

        <AlertDialogContent>
          <AlertDialogHeader>
            <AlertDialogTitle>Delete role</AlertDialogTitle>
            <AlertDialogDescription>
              Are you sure you want to delete the role{" "}
              <span className="font-semibold">{selectedRole?.name}</span>? This
              action cannot be undone. All group assignments using this role
              will be removed.
            </AlertDialogDescription>
          </AlertDialogHeader>
          <AlertDialogFooter>
            <AlertDialogCancel>Cancel</AlertDialogCancel>
            <AlertDialogAction
              variant="destructive"
              onClick={handleDeleteRole}
              disabled={deleteRoleIsPending}
            >
              {deleteRoleIsPending ? "Deleting..." : "Delete"}
            </AlertDialogAction>
          </AlertDialogFooter>
        </AlertDialogContent>
      </AlertDialog>

      {isCreateOpen && (
        <RoleFormDialog
          title="Create role"
          description="Create a new role with specific scopes. Roles can be assigned to groups to grant permissions."
          scopes={scopes}
          onSubmit={handleCreateRole}
          isPending={createRoleIsPending}
          onOpenChange={(open) => {
            if (!open) setIsCreateOpen(false)
          }}
        />
      )}

      {isEditOpen && selectedRole && (
        <RoleFormDialog
          title="Edit role"
          description="Update the role's name, description, and assigned scopes."
          scopes={scopes}
          initialData={selectedRole}
          onSubmit={(name, description, scopeIds) =>
            handleUpdateRole(selectedRole.id, name, description, scopeIds)
          }
          isPending={updateRoleIsPending}
          onOpenChange={(open) => {
            if (!open) {
              setIsEditOpen(false)
              setSelectedRole(null)
            }
          }}
        />
      )}
    </Dialog>
  )
}

function RoleFormDialog({
  title,
  description,
  scopes,
  initialData,
  onSubmit,
  isPending,
  onOpenChange,
}: {
  title: string
  description: string
  scopes: ScopeRead[]
  initialData?: RoleReadWithScopes
  onSubmit: (
    name: string,
    description: string,
    scopeIds: string[]
  ) => Promise<void>
  isPending: boolean
  onOpenChange: (open: boolean) => void
}) {
  const [name, setName] = useState(initialData?.name ?? "")
  const [roleDescription, setRoleDescription] = useState(
    initialData?.description ?? ""
  )
  const [selectedScopeIds, setSelectedScopeIds] = useState<Set<string>>(
    new Set(initialData?.scopes?.map((s) => s.id) ?? [])
  )
  const [scopeFilter, setScopeFilter] = useState("")

  const filteredScopes = scopes.filter(
    (scope) =>
      scope.name.toLowerCase().includes(scopeFilter.toLowerCase()) ||
      scope.description?.toLowerCase().includes(scopeFilter.toLowerCase())
  )

  // Group scopes by resource
  const groupedScopes = filteredScopes.reduce(
    (acc, scope) => {
      const resource = scope.resource
      if (!acc[resource]) {
        acc[resource] = []
      }
      acc[resource].push(scope)
      return acc
    },
    {} as Record<string, ScopeRead[]>
  )

  const toggleScope = useCallback((scopeId: string) => {
    setSelectedScopeIds((prev) => {
      const next = new Set(prev)
      if (next.has(scopeId)) {
        next.delete(scopeId)
      } else {
        next.add(scopeId)
      }
      return next
    })
  }, [])

  const handleSubmit = async (e: React.FormEvent) => {
    e.preventDefault()
    if (!name.trim()) return
    await onSubmit(
      name.trim(),
      roleDescription.trim(),
      Array.from(selectedScopeIds)
    )
  }

  return (
    <DialogContent className="max-w-2xl">
      <form onSubmit={handleSubmit}>
        <DialogHeader>
          <DialogTitle>{title}</DialogTitle>
          <DialogDescription>{description}</DialogDescription>
        </DialogHeader>
        <div className="space-y-4 py-4">
          <div className="grid gap-4 sm:grid-cols-2">
            <div className="space-y-2">
              <Label htmlFor="role-name">Role name</Label>
              <Input
                id="role-name"
                placeholder="e.g., Security Analyst"
                value={name}
                onChange={(e) => setName(e.target.value)}
                required
              />
            </div>
            <div className="space-y-2">
              <Label htmlFor="role-description">Description</Label>
              <Input
                id="role-description"
                placeholder="Optional description"
                value={roleDescription}
                onChange={(e) => setRoleDescription(e.target.value)}
              />
            </div>
          </div>

          <div className="space-y-2">
            <div className="flex items-center justify-between">
              <Label>Scopes ({selectedScopeIds.size} selected)</Label>
              <Input
                placeholder="Filter scopes..."
                value={scopeFilter}
                onChange={(e) => setScopeFilter(e.target.value)}
                className="w-[200px]"
              />
            </div>
            <ScrollArea className="h-[300px] rounded-md border p-4">
              {Object.entries(groupedScopes).map(
                ([resource, resourceScopes]) => (
                  <div key={resource} className="mb-4">
                    <h4 className="mb-2 text-sm font-semibold text-muted-foreground">
                      {resource}
                    </h4>
                    <div className="space-y-2">
                      {resourceScopes.map((scope) => (
                        <div
                          key={scope.id}
                          className="flex items-center space-x-2"
                        >
                          <Checkbox
                            id={scope.id}
                            checked={selectedScopeIds.has(scope.id)}
                            onCheckedChange={() => toggleScope(scope.id)}
                          />
                          <label
                            htmlFor={scope.id}
                            className="flex-1 cursor-pointer text-sm"
                          >
                            <code className="rounded bg-muted px-1 py-0.5 text-xs">
                              {scope.name}
                            </code>
                            {scope.description && (
                              <span className="ml-2 text-xs text-muted-foreground">
                                {scope.description}
                              </span>
                            )}
                          </label>
                        </div>
                      ))}
                    </div>
                  </div>
                )
              )}
              {Object.keys(groupedScopes).length === 0 && (
                <p className="text-center text-sm text-muted-foreground">
                  No scopes found
                </p>
              )}
            </ScrollArea>
          </div>
        </div>
        <DialogFooter>
          <Button
            type="button"
            variant="outline"
            onClick={() => onOpenChange(false)}
          >
            Cancel
          </Button>
          <Button type="submit" disabled={!name.trim() || isPending}>
            {isPending
              ? initialData
                ? "Saving..."
                : "Creating..."
              : initialData
                ? "Save changes"
                : "Create role"}
          </Button>
        </DialogFooter>
      </form>
    </DialogContent>
  )
}

const toolbarProps: DataTableToolbarProps<RoleReadWithScopes> = {
  filterProps: {
    placeholder: "Filter roles...",
    column: "name",
  },
}
