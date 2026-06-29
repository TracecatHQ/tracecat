"use client"

import { zodResolver } from "@hookform/resolvers/zod"
import { useEffect, useState } from "react"
import { useForm, useWatch } from "react-hook-form"
import { z } from "zod"
import type { GitHubAppRepository } from "@/client"
import { Button } from "@/components/ui/button"
import {
  Form,
  FormControl,
  FormDescription,
  FormField,
  FormItem,
  FormLabel,
  FormMessage,
} from "@/components/ui/form"
import { Input } from "@/components/ui/input"
import {
  Select,
  SelectContent,
  SelectGroup,
  SelectItem,
  SelectTrigger,
  SelectValue,
} from "@/components/ui/select"
import { ToggleTabs } from "@/components/ui/toggle-tabs"
import { validateGitSshUrl } from "@/lib/git"
import { useWorkspaceSettings } from "@/lib/hooks"

type RepositoryInputMode = "select" | "manual"

export const syncSettingsSchema = z.object({
  git_repo_url: z
    .string()
    .nullish()
    .transform((url) => url?.trim() || null)
    .superRefine((url, ctx) => validateGitSshUrl(url, ctx)),
})

type SyncSettingsForm = z.infer<typeof syncSettingsSchema>

interface WorkspaceSyncConnectionFormProps {
  workspaceId: string
  persistedGitUrl: string | undefined
  repositories: GitHubAppRepository[]
  repositoriesIsLoading: boolean
  repositoriesError: unknown
  onClose: () => void
}

/**
 * Connection editor for the workspace git remote. Operators either pick a
 * repository granted to the connected GitHub App installation or enter a
 * git+ssh URL manually, then the choice is persisted to workspace settings.
 */
export function WorkspaceSyncConnectionForm({
  workspaceId,
  persistedGitUrl,
  repositories,
  repositoriesIsLoading,
  repositoriesError,
  onClose,
}: WorkspaceSyncConnectionFormProps) {
  const { updateWorkspace, isUpdating } = useWorkspaceSettings(workspaceId)

  const form = useForm<SyncSettingsForm>({
    resolver: zodResolver(syncSettingsSchema),
    mode: "onChange",
    defaultValues: {
      git_repo_url: persistedGitUrl ?? "",
    },
  })
  const currentGitRepoUrl = useWatch({
    control: form.control,
    name: "git_repo_url",
  })

  const hasRepositoryOptions = repositories.length > 0
  const currentGitUrlMatchesRepository = repositories.some((repository) =>
    matchesRepositoryGitUrl(currentGitRepoUrl, repository)
  )
  const [repositoryInputMode, setRepositoryInputMode] =
    useState<RepositoryInputMode>("select")
  const [hasSelectedRepositoryInputMode, setHasSelectedRepositoryInputMode] =
    useState(false)
  useEffect(() => {
    if (
      !hasSelectedRepositoryInputMode &&
      hasRepositoryOptions &&
      !repositoriesIsLoading &&
      currentGitRepoUrl &&
      !currentGitUrlMatchesRepository
    ) {
      setRepositoryInputMode("manual")
    }
  }, [
    currentGitRepoUrl,
    currentGitUrlMatchesRepository,
    hasSelectedRepositoryInputMode,
    hasRepositoryOptions,
    repositoriesIsLoading,
  ])
  function handleRepositoryInputModeChange(mode: RepositoryInputMode) {
    setHasSelectedRepositoryInputMode(true)
    setRepositoryInputMode(mode)
  }

  const shouldShowRepositoryModeTabs =
    hasRepositoryOptions && !repositoriesIsLoading
  const shouldShowRepositorySelect =
    repositoriesIsLoading ||
    (hasRepositoryOptions && repositoryInputMode === "select")
  let repositoryDescription =
    "Git URL of the remote repository. Must use git+ssh scheme."
  if (hasRepositoryOptions && repositoryInputMode === "select") {
    repositoryDescription =
      "Select a repository granted to the connected GitHub App installation."
  } else if (hasRepositoryOptions) {
    repositoryDescription = "Enter any valid git+ssh URL manually."
  } else if (repositoriesError) {
    repositoryDescription =
      "Could not load GitHub App repositories. Enter a git+ssh URL manually."
  }

  async function onSubmit(values: SyncSettingsForm) {
    const selectedRepository =
      repositoryInputMode === "select"
        ? findMatchingRepository(values.git_repo_url, repositories)
        : undefined

    await updateWorkspace({
      settings: {
        git_repo_url: selectedRepository
          ? getRepositoryGitUrl(selectedRepository)
          : values.git_repo_url,
      },
    })
    onClose()
  }

  return (
    <Form {...form}>
      <form onSubmit={form.handleSubmit(onSubmit)} className="space-y-4">
        <FormField
          control={form.control}
          name="git_repo_url"
          render={({ field, fieldState }) => (
            <FormItem className="flex flex-col">
              <div className="flex items-center justify-between gap-3">
                <FormLabel>Remote repository URL</FormLabel>
                {shouldShowRepositoryModeTabs && (
                  <ToggleTabs<RepositoryInputMode>
                    size="sm"
                    showTooltips={false}
                    value={repositoryInputMode}
                    onValueChange={handleRepositoryInputModeChange}
                    options={[
                      { value: "select", content: "Select" },
                      { value: "manual", content: "Manual" },
                    ]}
                  />
                )}
              </div>
              {shouldShowRepositorySelect ? (
                <Select
                  disabled={repositoriesIsLoading}
                  onValueChange={(value) => {
                    const repository = repositories.find(
                      (repo) => repo.git_url === value
                    )
                    field.onChange(
                      repository ? getRepositoryGitUrl(repository) : value
                    )
                  }}
                  value={getRepositorySelectValue(field.value, repositories)}
                >
                  <FormControl>
                    <SelectTrigger aria-invalid={fieldState.invalid}>
                      <SelectValue
                        placeholder={
                          repositoriesIsLoading
                            ? "Loading repositories..."
                            : "Select a repository"
                        }
                      />
                    </SelectTrigger>
                  </FormControl>
                  <SelectContent>
                    <SelectGroup>
                      {field.value &&
                        !repositories.some((repository) =>
                          matchesRepositoryGitUrl(field.value, repository)
                        ) && (
                          <SelectItem value={field.value}>
                            {field.value}
                          </SelectItem>
                        )}
                      {repositories.map((repository) => (
                        <RepositorySelectItem
                          key={`${repository.installation_id}:${repository.id}`}
                          repository={repository}
                        />
                      ))}
                    </SelectGroup>
                  </SelectContent>
                </Select>
              ) : (
                <FormControl>
                  <Input
                    aria-invalid={fieldState.invalid}
                    placeholder="git+ssh://git@github.com/my-org/my-repo.git"
                    {...field}
                    value={field.value ?? ""}
                  />
                </FormControl>
              )}
              <FormDescription>{repositoryDescription}</FormDescription>
              <FormMessage />
            </FormItem>
          )}
        />

        <div className="flex items-center gap-2">
          <Button type="submit" disabled={isUpdating} size="sm">
            {isUpdating ? "Saving..." : "Save"}
          </Button>
          {persistedGitUrl && (
            <Button type="button" variant="ghost" size="sm" onClick={onClose}>
              Cancel
            </Button>
          )}
        </div>
      </form>
    </Form>
  )
}

function RepositorySelectItem({
  repository,
}: {
  repository: GitHubAppRepository
}) {
  return (
    <SelectItem value={repository.git_url}>{repository.full_name}</SelectItem>
  )
}

function getRepositoryGitUrl(repository: GitHubAppRepository) {
  const defaultBranch = repository.default_branch.trim()
  if (!defaultBranch || defaultBranch === "main") {
    return repository.git_url
  }
  return `${repository.git_url}@${defaultBranch}`
}

function matchesRepositoryGitUrl(
  gitUrl: string | null | undefined,
  repository: GitHubAppRepository
) {
  return (
    gitUrl === repository.git_url || gitUrl === getRepositoryGitUrl(repository)
  )
}

function findMatchingRepository(
  gitUrl: string | null | undefined,
  repositories: GitHubAppRepository[]
) {
  return repositories.find((repository) =>
    matchesRepositoryGitUrl(gitUrl, repository)
  )
}

function getRepositorySelectValue(
  gitUrl: string | null | undefined,
  repositories: GitHubAppRepository[]
) {
  const repository = findMatchingRepository(gitUrl, repositories)
  return repository?.git_url ?? gitUrl ?? ""
}
