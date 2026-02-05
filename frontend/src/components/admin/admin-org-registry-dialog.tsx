"use client"

import { useEffect, useState } from "react"
import type { OrgRegistryRepositoryRead } from "@/client"
import { AdminOrgRegistryTable } from "@/components/admin/admin-org-registry-table"
import { OrgRegistryVersionsPanel } from "@/components/admin/org-registry-versions-panel"
import {
  Breadcrumb,
  BreadcrumbItem,
  BreadcrumbLink,
  BreadcrumbList,
  BreadcrumbPage,
  BreadcrumbSeparator,
} from "@/components/ui/breadcrumb"
import { Badge } from "@/components/ui/badge"
import {
  Dialog,
  DialogContent,
  DialogDescription,
  DialogHeader,
  DialogTitle,
  DialogTrigger,
} from "@/components/ui/dialog"
import { ScrollArea } from "@/components/ui/scroll-area"
import { useAdminOrganization } from "@/hooks/use-admin"

interface AdminOrgRegistryDialogProps {
  orgId: string
  trigger?: React.ReactNode
  open?: boolean
  onOpenChange?: (open: boolean) => void
}

export function AdminOrgRegistryDialog({
  orgId,
  trigger,
  open: controlledOpen,
  onOpenChange,
}: AdminOrgRegistryDialogProps) {
  const [internalOpen, setInternalOpen] = useState(false)
  const [selectedRepository, setSelectedRepository] =
    useState<OrgRegistryRepositoryRead | null>(null)
  const isControlled = controlledOpen !== undefined
  const dialogOpen = isControlled ? controlledOpen : internalOpen
  const setDialogOpen = (nextOpen: boolean) => {
    if (!isControlled) {
      setInternalOpen(nextOpen)
    }
    onOpenChange?.(nextOpen)
  }
  const { organization } = useAdminOrganization(orgId)

  useEffect(() => {
    if (!dialogOpen) {
      setSelectedRepository(null)
    }
  }, [dialogOpen])

  const handleShowVersions = (repository: OrgRegistryRepositoryRead) => {
    setSelectedRepository(repository)
  }

  const handleBackToRepositories = () => {
    setSelectedRepository(null)
  }

  return (
    <Dialog open={dialogOpen} onOpenChange={setDialogOpen}>
      {trigger ? <DialogTrigger asChild>{trigger}</DialogTrigger> : null}
      <DialogContent className="max-w-5xl max-h-[80vh] p-0 overflow-hidden flex flex-col gap-0">
        <DialogHeader className="p-6 pb-4">
          {selectedRepository ? (
            <div className="space-y-2">
              <Breadcrumb>
                <BreadcrumbList className="flex-nowrap overflow-hidden whitespace-nowrap">
                  <BreadcrumbItem>
                    <BreadcrumbLink asChild className="font-semibold">
                      <button type="button" onClick={handleBackToRepositories}>
                        Repositories
                      </button>
                    </BreadcrumbLink>
                  </BreadcrumbItem>
                  <BreadcrumbSeparator className="shrink-0" />
                  <BreadcrumbItem>
                    <BreadcrumbPage className="font-semibold">
                      Versions
                    </BreadcrumbPage>
                  </BreadcrumbItem>
                  <BreadcrumbItem>
                    <Badge
                      variant="secondary"
                      className="max-w-[320px] font-mono font-normal"
                    >
                      <span className="truncate">
                        {selectedRepository.origin}
                      </span>
                    </Badge>
                  </BreadcrumbItem>
                </BreadcrumbList>
              </Breadcrumb>
            </div>
          ) : (
            <>
              <DialogTitle>Registry repositories</DialogTitle>
              <DialogDescription>
                Manage registry repositories for{" "}
                {organization?.name ?? "organization"}.
              </DialogDescription>
            </>
          )}
        </DialogHeader>
        <ScrollArea className="flex-1 px-6 pb-6">
          {selectedRepository ? (
            <OrgRegistryVersionsPanel
              orgId={orgId}
              repository={selectedRepository}
            />
          ) : (
            <AdminOrgRegistryTable
              orgId={orgId}
              onShowVersions={handleShowVersions}
            />
          )}
        </ScrollArea>
      </DialogContent>
    </Dialog>
  )
}
