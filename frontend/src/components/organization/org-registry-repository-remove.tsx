"use client"

import { GitBranchIcon } from "lucide-react"
import { useState } from "react"
import {
  type RegistryRepositoryReadMinimal,
  settingsUpdateGitSettings,
} from "@/client"
import { useScopeCheck } from "@/components/auth/scope-guard"
import { ConfirmDestructiveDialog } from "@/components/confirm-destructive-dialog"
import { isCustomRegistryOrigin } from "@/components/registry/utils"
import { Button } from "@/components/ui/button"
import { toast } from "@/components/ui/use-toast"
import { getApiErrorDetail } from "@/lib/errors"
import { useOrgGitSettings, useRegistryRepositories } from "@/lib/hooks"
import { useQueryClient } from "@/lib/query"

/**
 * Mirrors `safe_url` in `tracecat/parse.py`, which derives the repository
 * origin from the configured Git URL by dropping the query and fragment.
 */
function toRegistryOrigin(url: string): string {
  return url.trim().split(/[?#]/)[0]
}

/**
 * Remove section for the custom registry Repository page.
 *
 * Lists every custom (Git) registry repository the organization still has,
 * including ones left behind after the repository URL was cleared or changed,
 * and deletes the selected repository with its versions and actions. When the
 * repository is the configured one, the repository URL is cleared first so it
 * is not recreated on the next load.
 */
export function OrgRegistryRepositoryRemoveSection() {
  const canDelete = useScopeCheck("org:registry:delete") === true
  const canUpdateSettings = useScopeCheck("org:settings:update") === true
  const queryClient = useQueryClient()
  const { gitSettings } = useOrgGitSettings()
  const { repos, deleteRepo, deleteRepoIsPending } = useRegistryRepositories()
  const [repoPendingRemove, setRepoPendingRemove] =
    useState<RegistryRepositoryReadMinimal | null>(null)
  const [isClearingUrl, setIsClearingUrl] = useState(false)

  const customRepos = (repos ?? []).filter((repo) =>
    isCustomRegistryOrigin(repo.origin)
  )
  if (!canDelete || customRepos.length === 0) {
    return null
  }

  const configuredOrigin = gitSettings?.git_repo_url
    ? toRegistryOrigin(gitSettings.git_repo_url)
    : null
  const pendingIsConfigured =
    repoPendingRemove !== null && repoPendingRemove.origin === configuredOrigin
  const isRemoving = isClearingUrl || deleteRepoIsPending

  async function handleConfirmRemove() {
    if (!repoPendingRemove) {
      return
    }
    const repo = repoPendingRemove
    const clearUrl = repo.origin === configuredOrigin
    setRepoPendingRemove(null)
    if (clearUrl) {
      setIsClearingUrl(true)
      try {
        await settingsUpdateGitSettings({
          requestBody: { git_repo_url: null, git_repo_package_name: null },
        })
        await queryClient.invalidateQueries({
          queryKey: ["org-git-settings"],
        })
      } catch (error) {
        console.error("Failed to clear the repository URL", error)
        toast({
          title: "Failed to remove repository",
          description: `Couldn't clear the repository URL: ${getApiErrorDetail(error) ?? "Please try again."}`,
        })
        return
      } finally {
        setIsClearingUrl(false)
      }
    }
    try {
      await deleteRepo({ repositoryId: repo.id })
    } catch (error) {
      console.error("Failed to remove registry repository", error)
    }
  }

  return (
    <div className="space-y-2">
      <div className="space-y-1">
        <p className="text-sm font-medium leading-none">Remove repository</p>
        <p className="text-sm text-muted-foreground">
          Delete the custom registry with all of its synced versions and
          actions. The SSH key is kept.
        </p>
      </div>
      <ul className="divide-y rounded-md border">
        {customRepos.map((repo) => {
          const isConfigured = repo.origin === configuredOrigin
          const blocked = isConfigured && !canUpdateSettings
          return (
            <li key={repo.id} className="flex items-center gap-3 px-3 py-2.5">
              <GitBranchIcon className="size-4 shrink-0 text-muted-foreground" />
              <div className="min-w-0 flex-1 space-y-0.5">
                <p className="break-all font-mono text-sm">{repo.origin}</p>
                <p className="text-xs text-muted-foreground">
                  {isConfigured
                    ? "Connected repository"
                    : "No longer connected"}
                </p>
              </div>
              <Button
                type="button"
                variant="outline"
                size="sm"
                className="shrink-0 text-red-600 hover:text-red-600 dark:text-red-400 dark:hover:text-red-400"
                disabled={isRemoving || blocked}
                title={
                  blocked
                    ? "Removing the connected repository also needs the settings update permission"
                    : undefined
                }
                onClick={() => setRepoPendingRemove(repo)}
              >
                Remove
              </Button>
            </li>
          )
        })}
      </ul>

      <ConfirmDestructiveDialog
        open={repoPendingRemove !== null}
        onOpenChange={(open) => {
          if (!open) {
            setRepoPendingRemove(null)
          }
        }}
        title="Remove custom registry?"
        description={
          <p>
            This permanently deletes the repository with all of its synced
            versions and actions.
            {pendingIsConfigured &&
              " The repository URL is also cleared from these settings."}{" "}
            Workflows that use its actions fail until you connect and sync a
            repository again. The SSH key is kept.
          </p>
        }
        identifier={
          repoPendingRemove
            ? { label: "Repository URL", value: repoPendingRemove.origin }
            : undefined
        }
        confirmPhrase={repoPendingRemove?.origin ?? ""}
        confirmLabel="Remove"
        isPending={isRemoving}
        onConfirm={handleConfirmRemove}
      />
    </div>
  )
}
