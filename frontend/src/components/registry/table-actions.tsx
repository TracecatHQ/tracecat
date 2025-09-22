"use client"

import { CopyIcon, GitBranchIcon, RefreshCcw, TrashIcon } from "lucide-react"
import type { RegistryRepositoryReadMinimal } from "@/client"
import {
  DropdownMenuGroup,
  DropdownMenuItem,
} from "@/components/ui/dropdown-menu"
import { toast } from "@/components/ui/use-toast"
import { copyToClipboard } from "@/lib/utils"

interface RepositoryActionsProps {
  repository: RegistryRepositoryReadMinimal
  onSync: (repo: RegistryRepositoryReadMinimal) => void
  onDelete: (repo: RegistryRepositoryReadMinimal) => void
  onChangeCommit: (repo: RegistryRepositoryReadMinimal) => void
}

export function RepositoryActions({
  repository,
  onSync,
  onDelete,
  onChangeCommit,
}: RepositoryActionsProps) {
  const handleCopyOrigin = async (e: React.MouseEvent) => {
    e.stopPropagation()
    try {
      await copyToClipboard({ value: repository.origin })
      toast({
        title: "Repository origin copied",
        description: (
          <span className="flex flex-col space-y-2">
            <span className="inline-block">{repository.origin}</span>
          </span>
        ),
      })
    } catch (error) {
      console.error(error)
      toast({
        title: "Failed to copy repository origin",
        description: "Please try again or copy manually",
        variant: "destructive",
      })
    }
  }

  const handleCopyCommitSha = async (e: React.MouseEvent) => {
    e.stopPropagation()
    if (!repository.commit_sha) return

    try {
      await copyToClipboard({ value: repository.commit_sha })
      toast({
        title: "Commit SHA copied",
        description: (
          <span className="flex flex-col space-y-2">
            <span className="inline-block">{repository.commit_sha}</span>
          </span>
        ),
      })
    } catch (error) {
      console.error(error)
      toast({
        title: "Failed to copy commit SHA",
        description: "Please try again or copy manually",
        variant: "destructive",
      })
    }
  }

  const handleSyncClick = (e: React.MouseEvent) => {
    e.stopPropagation()
    onSync(repository)
  }

  const handleDeleteClick = (e: React.MouseEvent) => {
    e.stopPropagation()
    onDelete(repository)
  }

  const handleChangeCommitClick = (e: React.MouseEvent) => {
    e.stopPropagation()
    onChangeCommit(repository)
  }

  return (
    <DropdownMenuGroup>
      <DropdownMenuItem
        className="flex items-center text-xs"
        onClick={handleCopyOrigin}
      >
        <CopyIcon className="mr-2 size-4" />
        <span>Copy repository origin</span>
      </DropdownMenuItem>

      {repository.commit_sha && (
        <DropdownMenuItem
          className="flex items-center text-xs"
          onClick={handleCopyCommitSha}
        >
          <CopyIcon className="mr-2 size-4" />
          <span>Copy commit SHA</span>
        </DropdownMenuItem>
      )}

      {/* Admin-only actions would go here with proper permission check */}
      <DropdownMenuItem
        className="flex items-center text-xs"
        onClick={handleSyncClick}
      >
        <RefreshCcw className="mr-2 size-4" />
        <span>Sync from remote</span>
      </DropdownMenuItem>

      {repository.origin.startsWith("git+ssh://") && (
        <DropdownMenuItem
          className="flex items-center text-xs"
          onClick={handleChangeCommitClick}
        >
          <GitBranchIcon className="mr-2 size-4" />
          <span>Change commit</span>
        </DropdownMenuItem>
      )}

      <DropdownMenuItem
        className="flex items-center text-xs text-rose-600"
        onClick={handleDeleteClick}
      >
        <TrashIcon className="mr-2 size-4" />
        <span>Delete repository</span>
      </DropdownMenuItem>
    </DropdownMenuGroup>
  )
}
