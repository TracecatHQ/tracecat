"use client"

import { CheckIcon } from "lucide-react"
import { useEffect, useState } from "react"
import type {
  OrgRegistryRepositoryRead,
  OrgRegistryVersionRead,
} from "@/client"
import { Badge } from "@/components/ui/badge"
import { Button } from "@/components/ui/button"
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

interface OrgRegistryVersionsPanelProps {
  orgId: string
  repository: OrgRegistryRepositoryRead
}

export function OrgRegistryVersionsPanel({
  orgId,
  repository,
}: OrgRegistryVersionsPanelProps) {
  const { versions, isLoading } = useAdminOrgRepositoryVersions(
    orgId,
    repository.id
  )
  const { promoteVersion, promotePending } = useAdminOrgRegistry(orgId)
  const [promotingId, setPromotingId] = useState<string | null>(null)
  const [currentVersionId, setCurrentVersionId] = useState<string | null>(
    repository.current_version_id ?? null
  )

  useEffect(() => {
    setCurrentVersionId(repository.current_version_id ?? null)
  }, [repository.current_version_id, repository.id])

  const handlePromote = async (version: OrgRegistryVersionRead) => {
    setPromotingId(version.id)
    try {
      const result = await promoteVersion({
        repositoryId: repository.id,
        versionId: version.id,
      })
      setCurrentVersionId(result.current_version_id ?? null)
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

  if (isLoading) {
    return (
      <div className="py-8 text-center text-muted-foreground">
        Loading versions...
      </div>
    )
  }

  if (!versions || versions.length === 0) {
    return (
      <div className="py-8 text-center text-muted-foreground">
        No versions found. Sync the repository to create a version.
      </div>
    )
  }

  return (
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
          const isCurrent = currentVersionId === version.id
          const isPromoting = promotingId === version.id && promotePending

          return (
            <TableRow key={version.id}>
              <TableCell>
                <div className="flex items-center gap-2">
                  <span className="font-mono text-sm">{version.version}</span>
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
  )
}
