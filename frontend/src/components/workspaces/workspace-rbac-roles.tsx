"use client"

import { DotsHorizontalIcon } from "@radix-ui/react-icons"
import { InfoIcon, PlusIcon, SearchIcon, ShieldIcon } from "lucide-react"
import { useMemo, useState } from "react"
import type { RoleReadWithScopes } from "@/client"
import { useScopeCheck } from "@/components/auth/scope-guard"
import {
  RbacBadge,
  RbacDetailRow,
  RbacListContainer,
  RbacListEmpty,
  RbacListHeader,
  RbacListItem,
} from "@/components/organization/rbac-list-item"
import { RoleFormDialog } from "@/components/rbac/role-form-dialog"
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
import { Dialog, DialogTrigger } from "@/components/ui/dialog"
import {
  DropdownMenu,
  DropdownMenuContent,
  DropdownMenuItem,
  DropdownMenuSeparator,
  DropdownMenuTrigger,
} from "@/components/ui/dropdown-menu"
import { Input } from "@/components/ui/input"
import { Skeleton } from "@/components/ui/skeleton"
import { useRbacAssignments, useRbacRoles, useRbacScopes } from "@/lib/hooks"
import { groupScopesByResource } from "@/lib/rbac"

const workspaceCategoryFilter = (key: string) => key !== "organization"

export function WorkspaceRbacRoles({
  workspaceId,
  hideCreateButton = false,
}: {
  workspaceId: string
  hideCreateButton?: boolean
}) {
  const [selectedRole, setSelectedRole] = useState<RoleReadWithScopes | null>(
    null
  )
  const [expandedRoleId, setExpandedRoleId] = useState<string | null>(null)
  const [isCreateOpen, setIsCreateOpen] = useState(false)
  const [isEditOpen, setIsEditOpen] = useState(false)
  const [searchQuery, setSearchQuery] = useState("")
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
  const canCreateRole = useScopeCheck("org:rbac:create") === true
  const canUpdateRole = useScopeCheck("org:rbac:update") === true
  const canDeleteRole = useScopeCheck("org:rbac:delete") === true

  // Get assignments for this workspace to show which roles are in use
  const { assignments } = useRbacAssignments({ workspaceId })
  const rolesInUse = useMemo(
    () => new Set(assignments?.map((a) => a.role_id) ?? []),
    [assignments]
  )

  // Filter scopes to workspace-relevant ones (exclude org-level scopes)
  const workspaceScopes = useMemo(() => {
    if (!scopes) return []
    return scopes.filter((scope) => !scope.resource.startsWith("org"))
  }, [scopes])

  const filteredRoles = useMemo(() => {
    if (!searchQuery.trim()) return roles
    const query = searchQuery.toLowerCase()
    return roles.filter(
      (role) =>
        role.name.toLowerCase().includes(query) ||
        role.description?.toLowerCase().includes(query)
    )
  }, [roles, searchQuery])

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

  if (error) {
    return (
      <div className="flex items-center justify-center py-12 text-sm text-destructive">
        Failed to load roles
      </div>
    )
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
          <RbacListHeader
            left={
              <div className="relative">
                <SearchIcon className="absolute left-2.5 top-1/2 size-4 -translate-y-1/2 text-muted-foreground" />
                <Input
                  placeholder="Search roles..."
                  value={searchQuery}
                  onChange={(e) => setSearchQuery(e.target.value)}
                  className="h-9 w-[250px] pl-8"
                />
              </div>
            }
            right={
              hideCreateButton || !canCreateRole ? null : (
                <DialogTrigger asChild>
                  <Button size="sm" onClick={() => setIsCreateOpen(true)}>
                    <PlusIcon className="mr-2 size-4" />
                    Create role
                  </Button>
                </DialogTrigger>
              )
            }
          />

          <div className="flex items-start gap-3 rounded-md border border-muted bg-muted/30 p-3 text-sm text-muted-foreground">
            <InfoIcon className="mt-0.5 size-4 shrink-0" />
            <div>
              <p className="font-medium text-foreground">How roles work</p>
              <p className="mt-1">
                Roles define permissions and are shared across your
                organization. To grant access in this workspace, assign roles to
                groups in the Groups tab.
              </p>
            </div>
          </div>

          {isLoading ? (
            <RbacListContainer>
              {[1, 2, 3].map((i) => (
                <div
                  key={i}
                  className="flex items-center gap-3 px-3 py-2.5 border-b border-border/50 last:border-b-0"
                >
                  <Skeleton className="size-6" />
                  <Skeleton className="size-4" />
                  <div className="flex-1 space-y-1.5">
                    <Skeleton className="h-4 w-32" />
                    <Skeleton className="h-3 w-48" />
                  </div>
                </div>
              ))}
            </RbacListContainer>
          ) : filteredRoles.length === 0 ? (
            <RbacListContainer>
              <RbacListEmpty
                message={
                  searchQuery ? "No roles match your search" : "No roles found"
                }
              />
            </RbacListContainer>
          ) : (
            <RbacListContainer>
              {filteredRoles.map((role) => (
                <RoleListItem
                  key={role.id}
                  role={role}
                  isAssigned={rolesInUse.has(role.id)}
                  isExpanded={expandedRoleId === role.id}
                  onExpandedChange={(expanded) =>
                    setExpandedRoleId(expanded ? role.id : null)
                  }
                  onEdit={() => {
                    setSelectedRole(role)
                    setIsEditOpen(true)
                  }}
                  onDelete={() => setSelectedRole(role)}
                  canUpdateRole={canUpdateRole}
                  canDeleteRole={canDeleteRole}
                />
              ))}
            </RbacListContainer>
          )}
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
          description="Create a role for your organization. Once created, you can assign it to groups to grant permissions within this workspace."
          scopes={workspaceScopes}
          onSubmit={handleCreateRole}
          isPending={createRoleIsPending}
          onOpenChange={(open) => {
            if (!open) setIsCreateOpen(false)
          }}
          categoryFilter={workspaceCategoryFilter}
        />
      )}

      {isEditOpen && selectedRole && (
        <RoleFormDialog
          title="Edit role"
          description="Update the role's name, description, and permissions. Changes apply organization-wide."
          scopes={workspaceScopes}
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
          categoryFilter={workspaceCategoryFilter}
        />
      )}
    </Dialog>
  )
}

function RoleListItem({
  role,
  isAssigned,
  isExpanded,
  onExpandedChange,
  onEdit,
  onDelete,
  canUpdateRole,
  canDeleteRole,
}: {
  role: RoleReadWithScopes
  isAssigned: boolean
  isExpanded: boolean
  onExpandedChange: (expanded: boolean) => void
  onEdit: () => void
  onDelete: () => void
  canUpdateRole: boolean
  canDeleteRole: boolean
}) {
  return (
    <RbacListItem
      icon={<ShieldIcon className="size-4" />}
      title={role.name}
      subtitle={role.description || `${role.scopes?.length ?? 0} scopes`}
      badges={
        <>
          {role.is_system && <RbacBadge variant="preset">Preset</RbacBadge>}
          {isAssigned && (
            <Badge variant="secondary" className="text-[10px]">
              Assigned
            </Badge>
          )}
        </>
      }
      isExpanded={isExpanded}
      onExpandedChange={onExpandedChange}
      actions={
        <DropdownMenu>
          <DropdownMenuTrigger asChild>
            <Button
              variant="ghost"
              className="size-8 p-0 opacity-0 transition-opacity group-hover:opacity-100 data-[state=open]:opacity-100"
            >
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
            {!role.is_system && (
              <>
                {(canUpdateRole || canDeleteRole) && <DropdownMenuSeparator />}
                {canUpdateRole && (
                  <DialogTrigger asChild>
                    <DropdownMenuItem onClick={onEdit}>
                      Edit role
                    </DropdownMenuItem>
                  </DialogTrigger>
                )}
                {canDeleteRole && (
                  <AlertDialogTrigger asChild>
                    <DropdownMenuItem
                      className="text-rose-500 focus:text-rose-600"
                      onClick={onDelete}
                    >
                      Delete role
                    </DropdownMenuItem>
                  </AlertDialogTrigger>
                )}
              </>
            )}
          </DropdownMenuContent>
        </DropdownMenu>
      }
    >
      <RoleDetails role={role} />
    </RbacListItem>
  )
}

function RoleDetails({ role }: { role: RoleReadWithScopes }) {
  // Filter to workspace-relevant scopes (exclude org-level)
  const workspaceScopes = useMemo(() => {
    if (!role.scopes) return []
    return role.scopes.filter((scope) => !scope.resource.startsWith("org"))
  }, [role.scopes])

  const groupedScopes = useMemo(
    () => groupScopesByResource(workspaceScopes),
    [workspaceScopes]
  )

  return (
    <div className="space-y-3">
      {role.description && (
        <RbacDetailRow label="Description">{role.description}</RbacDetailRow>
      )}
      <RbacDetailRow label="Workspace scopes">
        <span className="text-muted-foreground">
          {workspaceScopes.length} permission
          {workspaceScopes.length !== 1 && "s"}
        </span>
      </RbacDetailRow>
      {workspaceScopes.length > 0 && (
        <div className="mt-2 space-y-2">
          {Object.entries(groupedScopes).map(([resource, resourceScopes]) => (
            <div key={resource}>
              <div className="mb-1 text-xs font-medium text-muted-foreground">
                {resource}
              </div>
              <div className="flex flex-wrap gap-1">
                {resourceScopes.map((scope) => (
                  <code
                    key={scope.id}
                    className="rounded bg-muted/60 px-1.5 py-0.5 text-[10px] font-mono"
                  >
                    {scope.action}
                  </code>
                ))}
              </div>
            </div>
          ))}
        </div>
      )}
    </div>
  )
}
