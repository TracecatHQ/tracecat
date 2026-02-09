"use client"

import { useEffect, useMemo, useState } from "react"
import { Badge } from "@/components/ui/badge"
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
import { Input } from "@/components/ui/input"
import {
  Table,
  TableBody,
  TableCell,
  TableHead,
  TableHeader,
  TableRow,
} from "@/components/ui/table"
import { toast } from "@/components/ui/use-toast"
import { useAdminOrganization, useAdminOrgDomains } from "@/hooks/use-admin"

interface AdminOrgDomainsDialogProps {
  orgId: string
  trigger?: React.ReactNode
  open?: boolean
  onOpenChange?: (open: boolean) => void
}

interface ApiErrorLike {
  body?: {
    detail?: unknown
  }
}

function getErrorDetail(error: unknown, fallback: string): string {
  if (typeof error === "object" && error !== null) {
    const apiError = error as ApiErrorLike
    if (typeof apiError.body?.detail === "string") {
      return apiError.body.detail
    }
  }
  return fallback
}

export function AdminOrgDomainsDialog({
  orgId,
  trigger,
  open: controlledOpen,
  onOpenChange,
}: AdminOrgDomainsDialogProps) {
  const [internalOpen, setInternalOpen] = useState(false)
  const [newDomain, setNewDomain] = useState("")
  const [setPrimaryOnCreate, setSetPrimaryOnCreate] = useState(false)
  const isControlled = controlledOpen !== undefined
  const dialogOpen = isControlled ? controlledOpen : internalOpen
  const setDialogOpen = (nextOpen: boolean) => {
    if (!isControlled) {
      setInternalOpen(nextOpen)
    }
    onOpenChange?.(nextOpen)
  }

  const { organization } = useAdminOrganization(orgId)
  const {
    domains,
    isLoading,
    createDomain,
    createPending,
    updateDomain,
    updatePending,
    deleteDomain,
    deletePending,
  } = useAdminOrgDomains(orgId)

  useEffect(() => {
    if (!dialogOpen) {
      setNewDomain("")
      setSetPrimaryOnCreate(false)
    }
  }, [dialogOpen])

  const hasDomains = (domains?.length ?? 0) > 0
  const isBusy = createPending || updatePending || deletePending

  const sortedDomains = useMemo(() => domains ?? [], [domains])

  const handleAddDomain = async () => {
    const domain = newDomain.trim()
    if (!domain) {
      return
    }

    try {
      await createDomain({
        domain,
        is_primary: setPrimaryOnCreate,
      })
      toast({
        title: "Domain added",
        description: `${domain} was assigned.`,
      })
      setNewDomain("")
      setSetPrimaryOnCreate(false)
    } catch (error) {
      toast({
        title: "Failed to add domain",
        description: getErrorDetail(error, "Please try again."),
        variant: "destructive",
      })
    }
  }

  const handleSetPrimary = async (domainId: string, domain: string) => {
    try {
      await updateDomain({
        domainId,
        data: { is_primary: true },
      })
      toast({
        title: "Primary domain updated",
        description: `${domain} is now primary.`,
      })
    } catch (error) {
      toast({
        title: "Failed to set primary domain",
        description: getErrorDetail(error, "Please try again."),
        variant: "destructive",
      })
    }
  }

  const handleToggleActive = async (
    domainId: string,
    domain: string,
    isCurrentlyActive: boolean
  ) => {
    const nextActive = !isCurrentlyActive
    try {
      await updateDomain({
        domainId,
        data: { is_active: nextActive },
      })
      toast({
        title: nextActive ? "Domain activated" : "Domain deactivated",
        description: `${domain} was ${nextActive ? "activated" : "deactivated"}.`,
      })
    } catch (error) {
      toast({
        title: "Failed to update domain",
        description: getErrorDetail(error, "Please try again."),
        variant: "destructive",
      })
    }
  }

  const handleDeleteDomain = async (domainId: string, domain: string) => {
    try {
      await deleteDomain(domainId)
      toast({
        title: "Domain deleted",
        description: `${domain} was removed.`,
      })
    } catch (error) {
      toast({
        title: "Failed to delete domain",
        description: getErrorDetail(error, "Please try again."),
        variant: "destructive",
      })
    }
  }

  return (
    <Dialog open={dialogOpen} onOpenChange={setDialogOpen}>
      {trigger ? <DialogTrigger asChild>{trigger}</DialogTrigger> : null}
      <DialogContent className="max-h-[80vh] max-w-4xl overflow-hidden">
        <DialogHeader>
          <DialogTitle>Organization domains</DialogTitle>
          <DialogDescription>
            Manage domains for {organization?.name ?? "organization"}.
          </DialogDescription>
        </DialogHeader>

        <div className="space-y-4 overflow-auto">
          <div className="flex flex-col gap-3 rounded-md border p-3">
            <div className="flex items-center gap-2">
              <Input
                value={newDomain}
                onChange={(event) => setNewDomain(event.target.value)}
                placeholder="example.com"
                disabled={isBusy}
              />
              <Button
                onClick={handleAddDomain}
                disabled={isBusy || newDomain.trim().length === 0}
              >
                {createPending ? "Adding..." : "Add domain"}
              </Button>
            </div>
            <label className="flex items-center gap-2 text-sm text-muted-foreground">
              <Checkbox
                checked={setPrimaryOnCreate}
                onCheckedChange={(checked) =>
                  setSetPrimaryOnCreate(checked === true)
                }
                disabled={isBusy}
              />
              Set as primary domain
            </label>
          </div>

          <div className="rounded-md border">
            <Table>
              <TableHeader>
                <TableRow>
                  <TableHead>Domain</TableHead>
                  <TableHead>Normalized</TableHead>
                  <TableHead>Status</TableHead>
                  <TableHead>Verification</TableHead>
                  <TableHead className="text-right">Actions</TableHead>
                </TableRow>
              </TableHeader>
              <TableBody>
                {isLoading ? (
                  <TableRow>
                    <TableCell className="text-muted-foreground" colSpan={5}>
                      Loading...
                    </TableCell>
                  </TableRow>
                ) : !hasDomains ? (
                  <TableRow>
                    <TableCell className="text-muted-foreground" colSpan={5}>
                      No domains assigned.
                    </TableCell>
                  </TableRow>
                ) : (
                  sortedDomains.map((domain) => (
                    <TableRow key={domain.id}>
                      <TableCell className="font-medium">
                        {domain.domain}
                      </TableCell>
                      <TableCell className="font-mono text-xs text-muted-foreground">
                        {domain.normalized_domain}
                      </TableCell>
                      <TableCell>
                        <div className="flex items-center gap-2">
                          {domain.is_primary ? (
                            <Badge variant="secondary">Primary</Badge>
                          ) : null}
                          {domain.is_active ? (
                            <Badge variant="outline">Active</Badge>
                          ) : (
                            <Badge variant="outline">Inactive</Badge>
                          )}
                        </div>
                      </TableCell>
                      <TableCell className="text-muted-foreground">
                        {domain.verification_method}
                      </TableCell>
                      <TableCell>
                        <div className="flex items-center justify-end gap-2">
                          <Button
                            variant="outline"
                            size="sm"
                            disabled={
                              isBusy || domain.is_primary || !domain.is_active
                            }
                            onClick={() =>
                              handleSetPrimary(domain.id, domain.domain)
                            }
                          >
                            Set primary
                          </Button>
                          <Button
                            variant="outline"
                            size="sm"
                            disabled={isBusy}
                            onClick={() =>
                              handleToggleActive(
                                domain.id,
                                domain.domain,
                                domain.is_active
                              )
                            }
                          >
                            {domain.is_active ? "Deactivate" : "Activate"}
                          </Button>
                          <Button
                            variant="ghost"
                            size="sm"
                            className="text-rose-600 hover:text-rose-700"
                            disabled={isBusy}
                            onClick={() =>
                              handleDeleteDomain(domain.id, domain.domain)
                            }
                          >
                            Delete
                          </Button>
                        </div>
                      </TableCell>
                    </TableRow>
                  ))
                )}
              </TableBody>
            </Table>
          </div>
        </div>

        <DialogFooter>
          <Button variant="outline" onClick={() => setDialogOpen(false)}>
            Close
          </Button>
        </DialogFooter>
      </DialogContent>
    </Dialog>
  )
}
