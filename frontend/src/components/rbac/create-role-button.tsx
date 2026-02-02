"use client"

import { PlusIcon } from "lucide-react"
import { useState } from "react"
import { RoleFormDialog } from "@/components/rbac/role-form-dialog"
import { Button } from "@/components/ui/button"
import { Dialog, DialogTrigger } from "@/components/ui/dialog"
import { useRbacRoles, useRbacScopes } from "@/lib/hooks"

interface CreateRoleButtonProps {
  /** Filter for workspace-only scopes (excludes org-level scopes) */
  workspaceOnly?: boolean
}

export function CreateRoleButton({
  workspaceOnly = false,
}: CreateRoleButtonProps) {
  const [isOpen, setIsOpen] = useState(false)
  const { createRole, createRoleIsPending } = useRbacRoles()
  const { scopes } = useRbacScopes({ includeSystem: true })

  // Filter scopes based on context
  const filteredScopes = workspaceOnly
    ? (scopes?.filter((scope) => !scope.resource.startsWith("org")) ?? [])
    : (scopes ?? [])

  const categoryFilter = workspaceOnly
    ? (key: string) => key !== "organization"
    : undefined

  const handleCreate = async (
    name: string,
    description: string,
    scopeIds: string[]
  ) => {
    await createRole({
      name,
      description: description || undefined,
      scope_ids: scopeIds,
    })
    setIsOpen(false)
  }

  return (
    <Dialog open={isOpen} onOpenChange={setIsOpen}>
      <DialogTrigger asChild>
        <Button variant="outline" size="sm" className="h-7 bg-white">
          <PlusIcon className="mr-1 h-3.5 w-3.5" />
          Create role
        </Button>
      </DialogTrigger>
      {isOpen && (
        <RoleFormDialog
          title="Create role"
          description={
            workspaceOnly
              ? "Create a role for your organization. Once created, you can assign it to groups to grant permissions within this workspace."
              : "Create a new role with specific scopes. Roles can be assigned to groups to grant permissions."
          }
          scopes={filteredScopes}
          onSubmit={handleCreate}
          isPending={createRoleIsPending}
          onOpenChange={setIsOpen}
          categoryFilter={categoryFilter}
        />
      )}
    </Dialog>
  )
}
