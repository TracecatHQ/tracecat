"use client"

import { useState } from "react"
import { AdminOrgRegistryTable } from "@/components/admin/admin-org-registry-table"
import {
  Dialog,
  DialogContent,
  DialogDescription,
  DialogHeader,
  DialogTitle,
  DialogTrigger,
} from "@/components/ui/dialog"
import { useAdminOrganization } from "@/hooks/use-admin"

interface AdminOrgRegistryDialogProps {
  orgId: string
  trigger: React.ReactNode
}

export function AdminOrgRegistryDialog({
  orgId,
  trigger,
}: AdminOrgRegistryDialogProps) {
  const [open, setOpen] = useState(false)
  const { organization } = useAdminOrganization(orgId)

  return (
    <Dialog open={open} onOpenChange={setOpen}>
      <DialogTrigger asChild>{trigger}</DialogTrigger>
      <DialogContent className="max-w-5xl">
        <DialogHeader>
          <DialogTitle>Registry repositories</DialogTitle>
          <DialogDescription>
            Manage registry repositories for{" "}
            {organization?.name ?? "organization"}.
          </DialogDescription>
        </DialogHeader>
        <div className="max-h-[70vh] overflow-auto">
          <AdminOrgRegistryTable orgId={orgId} />
        </div>
      </DialogContent>
    </Dialog>
  )
}
