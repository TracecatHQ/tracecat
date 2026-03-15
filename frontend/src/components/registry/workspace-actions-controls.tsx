"use client"

import {
  ChevronDown,
  CopyIcon,
  GitBranchIcon,
  HistoryIcon,
  RefreshCcw,
} from "lucide-react"
import Link from "next/link"
import { useEffect, useState } from "react"
import type { RegistryRepositoryReadMinimal } from "@/client"
import { useScopeCheck } from "@/components/auth/scope-guard"
import { CommitSelectorDialog } from "@/components/registry/dialogs/repository-commit-dialog"
import { SyncRepositoryDialog } from "@/components/registry/dialogs/repository-sync-dialog"
import { RepositoryVersionsDialog } from "@/components/registry/dialogs/repository-versions-dialog"
import { getCustomRegistryRepository } from "@/components/registry/utils"
import { Button } from "@/components/ui/button"
import {
  DropdownMenu,
  DropdownMenuContent,
  DropdownMenuItem,
  DropdownMenuSeparator,
  DropdownMenuTrigger,
} from "@/components/ui/dropdown-menu"
import { toast } from "@/components/ui/use-toast"
import { useRegistryRepositories } from "@/lib/hooks"
import { copyToClipboard } from "@/lib/utils"

type ActiveDialog = "sync" | "commit" | "versions" | null

function RegistryActionsControlsMenu() {
  const canUpdateRegistry = useScopeCheck("org:registry:update") === true
  const { repos, syncRepo, syncRepoIsPending } = useRegistryRepositories()
  const customRepo = getCustomRegistryRepository(repos)
  const showAddRegistry = canUpdateRegistry
  const showRegistryActions = canUpdateRegistry && !!customRepo
  const showCopyOrigin = !!customRepo
  const hasVisibleActions =
    showAddRegistry || showRegistryActions || showCopyOrigin
  const [activeDialog, setActiveDialog] = useState<ActiveDialog>(null)
  const [selectedRepo, setSelectedRepo] =
    useState<RegistryRepositoryReadMinimal | null>(customRepo)

  useEffect(() => {
    if (!selectedRepo || !repos) {
      return
    }

    const nextRepo = repos.find((repo) => repo.id === selectedRepo.id)
    if (nextRepo && nextRepo !== selectedRepo) {
      setSelectedRepo(nextRepo)
    }
  }, [repos, selectedRepo])

  useEffect(() => {
    if (!customRepo) {
      setSelectedRepo(null)
      return
    }

    setSelectedRepo((current) => current ?? customRepo)
  }, [customRepo])

  const handleOpenDialog = (dialog: Exclude<ActiveDialog, null>) => {
    if (!customRepo) {
      return
    }
    setSelectedRepo(customRepo)
    setActiveDialog(dialog)
  }

  const handleCopyOrigin = async () => {
    if (!customRepo) {
      return
    }

    try {
      await copyToClipboard({ value: customRepo.origin })
      toast({
        title: "Repository origin copied",
        description: customRepo.origin,
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

  if (!hasVisibleActions) {
    return null
  }

  return (
    <>
      <DropdownMenu>
        <DropdownMenuTrigger asChild>
          <Button variant="outline" size="sm" className="h-7 bg-white">
            <RefreshCcw
              className={
                syncRepoIsPending
                  ? "mr-1.5 size-3.5 animate-spin"
                  : "mr-1.5 size-3.5"
              }
            />
            Manage
            <ChevronDown className="ml-1 size-3.5" />
          </Button>
        </DropdownMenuTrigger>
        <DropdownMenuContent align="end">
          {showAddRegistry ? (
            <DropdownMenuItem asChild>
              <Link href="/organization/settings/custom-registry">
                <GitBranchIcon className="mr-2 size-4" />
                <span>Add registry</span>
              </Link>
            </DropdownMenuItem>
          ) : null}

          {customRepo ? (
            <>
              {showAddRegistry && showRegistryActions ? (
                <DropdownMenuSeparator />
              ) : null}

              {showRegistryActions ? (
                <DropdownMenuItem onSelect={() => handleOpenDialog("sync")}>
                  <RefreshCcw className="mr-2 size-4" />
                  <span>Sync from remote</span>
                </DropdownMenuItem>
              ) : null}

              {showRegistryActions ? (
                <DropdownMenuItem onSelect={() => handleOpenDialog("commit")}>
                  <GitBranchIcon className="mr-2 size-4" />
                  <span>Change commit</span>
                </DropdownMenuItem>
              ) : null}

              {showRegistryActions ? (
                <DropdownMenuItem onSelect={() => handleOpenDialog("versions")}>
                  <HistoryIcon className="mr-2 size-4" />
                  <span>Manage versions</span>
                </DropdownMenuItem>
              ) : null}

              {showCopyOrigin ? (
                <DropdownMenuItem onSelect={handleCopyOrigin}>
                  <CopyIcon className="mr-2 size-4" />
                  <span>Copy repo origin</span>
                </DropdownMenuItem>
              ) : null}
            </>
          ) : null}
        </DropdownMenuContent>
      </DropdownMenu>

      <SyncRepositoryDialog
        open={activeDialog === "sync"}
        onOpenChange={(open) => {
          if (!open) {
            setActiveDialog(null)
          }
        }}
        selectedRepo={selectedRepo}
        setSelectedRepo={setSelectedRepo}
        syncRepo={syncRepo}
        syncRepoIsPending={syncRepoIsPending}
      />

      <CommitSelectorDialog
        open={activeDialog === "commit"}
        onOpenChange={(open) => {
          if (!open) {
            setActiveDialog(null)
          }
        }}
        selectedRepo={selectedRepo}
        initialCommitSha={selectedRepo?.commit_sha}
      />

      <RepositoryVersionsDialog
        open={activeDialog === "versions"}
        onOpenChange={(open) => {
          if (!open) {
            setActiveDialog(null)
          }
        }}
        selectedRepo={selectedRepo}
      />
    </>
  )
}

export function RegistryActionsControls() {
  const canAdministerOrg = useScopeCheck("org:update")

  if (canAdministerOrg !== true) {
    return null
  }

  return <RegistryActionsControlsMenu />
}
