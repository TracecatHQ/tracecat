"use client"

import { CheckIcon, HistoryIcon } from "lucide-react"
import { useState } from "react"
import type {
  OrgRegistryRepositoryRead,
  OrgRegistryVersionRead,
} from "@/client"
import { Badge } from "@/components/ui/badge"
import { Button } from "@/components/ui/button"
import {
  Dialog,
  DialogContent,
  DialogDescription,
  DialogHeader,
  DialogTitle,
  DialogTrigger,
} from "@/components/ui/dialog"
import {
  Table,
  TableBody,
  TableCell,
  TableHead,
  TableHeader,
  TableRow,
} from "@/components/ui/table"
import { toast } from "@/components/ui/use-toast"
import {
  useAdminOrgRegistry,
  useAdminOrgRepositoryVersions,
} from "@/hooks/use-admin"
import { getRelativeTime } from "@/lib/event-history"

interface OrgRegistryVersionsDialogProps {
  orgId: string
  repository: OrgRegistryRepositoryRead
}

export function OrgRegistryVersionsDialog({
  orgId,
  repository,
}: OrgRegistryVersionsDialogProps) {
  const [open, setOpen] = useState(false)
  const { versions, isLoading } = useAdminOrgRepositoryVersions(
    orgId,
    repository.id
  )
  const { promoteVersion, promotePending } = useAdminOrgRegistry(orgId)
  const [promotingId, setPromotingId] = useState<string | null>(null)

  const handlePromote = async (version: OrgRegistryVersionRead) => {
    setPromotingId(version.id)
    try {
      await promoteVersion({
        repositoryId: repository.id,
        versionId: version.id,
      })
      toast({
        title: "Version promoted",
        description: `Version ${version.version} is now the current version.`,
      })
    } catch (error) {
      console.error("Failed to promote version", error)
      toast({
        title: "Failed to promote version",
        description: "Please try again.",
        variant: "destructive",
      })
    } finally {
      setPromotingId(null)
    }
  }

  return (
    <Dialog open={open} onOpenChange={setOpen}>
      <DialogTrigger asChild>
        <Button variant="ghost" size="sm" className="gap-1">
          <HistoryIcon className="size-3" />
          Versions
        </Button>
      </DialogTrigger>
      <DialogContent className="max-w-2xl">
        <DialogHeader>
          <DialogTitle>Repository versions</DialogTitle>
          <DialogDescription className="font-mono text-xs">
            {repository.origin}
          </DialogDescription>
        </DialogHeader>
        <div className="max-h-[400px] overflow-auto">
          {isLoading ? (
            <div className="py-8 text-center text-muted-foreground">
              Loading versions...
            </div>
          ) : !versions || versions.length === 0 ? (
            <div className="py-8 text-center text-muted-foreground">
              No versions found. Sync the repository to create a version.
            </div>
          ) : (
            <Table>
              <TableHeader>
                <TableRow>
                  <TableHead>Version</TableHead>
                  <TableHead>Commit</TableHead>
                  <TableHead>Created</TableHead>
                  <TableHead className="w-[100px]">Actions</TableHead>
                </TableRow>
              </TableHeader>
              <TableBody>
                {versions.map((version) => {
                  const isCurrent = repository.current_version_id === version.id
                  const isPromoting =
                    promotingId === version.id && promotePending

                  return (
                    <TableRow key={version.id}>
                      <TableCell>
                        <div className="flex items-center gap-2">
                          <span className="font-mono text-sm">
                            {version.version}
                          </span>
                          {isCurrent && (
                            <Badge
                              variant="default"
                              className="bg-green-500 hover:bg-green-600"
                            >
                              Current
                            </Badge>
                          )}
                        </div>
                      </TableCell>
                      <TableCell>
                        <code className="text-xs text-muted-foreground">
                          {version.commit_sha?.substring(0, 7) ?? "-"}
                        </code>
                      </TableCell>
                      <TableCell>
                        <span className="text-xs text-muted-foreground">
                          {getRelativeTime(new Date(version.created_at))}
                        </span>
                      </TableCell>
                      <TableCell>
                        {!isCurrent && (
                          <Button
                            variant="ghost"
                            size="sm"
                            onClick={() => handlePromote(version)}
                            disabled={isPromoting}
                          >
                            {isPromoting ? (
                              "Promoting..."
                            ) : (
                              <>
                                <CheckIcon className="mr-1 size-3" />
                                Promote
                              </>
                            )}
                          </Button>
                        )}
                      </TableCell>
                    </TableRow>
                  )
                })}
              </TableBody>
            </Table>
          )}
        </div>
      </DialogContent>
    </Dialog>
  )
}
