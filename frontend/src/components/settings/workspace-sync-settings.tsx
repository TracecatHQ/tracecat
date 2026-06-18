"use client"

import { zodResolver } from "@hookform/resolvers/zod"
import { GitPullRequestIcon } from "lucide-react"
import { useState } from "react"
import { useForm } from "react-hook-form"
import { z } from "zod"
import type { GitHubAppRepository, WorkspaceRead } from "@/client"
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
import { validateGitSshUrl } from "@/lib/git"
import { useGitHubAppRepositories, useWorkspaceSettings } from "@/lib/hooks"
import { WorkflowPullDialog } from "../organization/workflow-pull-dialog"

export const syncSettingsSchema = z.object({
  git_repo_url: z
    .string()
    .nullish()
    .transform((url) => url?.trim() || null)
    .superRefine((url, ctx) => validateGitSshUrl(url, ctx)),
})

type SyncSettingsForm = z.infer<typeof syncSettingsSchema>

interface WorkspaceSyncSettingsProps {
  workspace: WorkspaceRead
}

export function WorkspaceSyncSettings({
  workspace,
}: WorkspaceSyncSettingsProps) {
  const [pullDialogOpen, setPullDialogOpen] = useState(false)
  const { updateWorkspace, isUpdating } = useWorkspaceSettings(workspace.id)
  const {
    repositories = [],
    repositoriesIsLoading,
    repositoriesError,
  } = useGitHubAppRepositories(workspace.id)

  const form = useForm<SyncSettingsForm>({
    resolver: zodResolver(syncSettingsSchema),
    mode: "onChange",
    defaultValues: {
      git_repo_url: workspace.settings?.git_repo_url || "",
    },
  })

  async function onSubmit(values: SyncSettingsForm) {
    await updateWorkspace({
      settings: {
        git_repo_url: values.git_repo_url,
      },
    })
  }

  const persistedGitUrl = workspace.settings?.git_repo_url || undefined
  const hasRepositoryOptions = repositories.length > 0
  const shouldShowRepositorySelect =
    repositoriesIsLoading || hasRepositoryOptions
  let repositoryDescription =
    "Git URL of the remote repository. Must use git+ssh scheme."
  if (hasRepositoryOptions) {
    repositoryDescription =
      "Select a repository granted to the connected GitHub App installation."
  } else if (repositoriesError) {
    repositoryDescription =
      "Could not load GitHub App repositories. Enter a git+ssh URL manually."
  }

  return (
    <div className="flex flex-col gap-6">
      <Form {...form}>
        <form
          onSubmit={form.handleSubmit(onSubmit)}
          className="flex flex-col gap-6"
        >
          <FormField
            control={form.control}
            name="git_repo_url"
            render={({ field, fieldState }) => (
              <FormItem className="flex flex-col">
                <FormLabel>Remote repository URL</FormLabel>
                {shouldShowRepositorySelect ? (
                  <Select
                    disabled={repositoriesIsLoading}
                    onValueChange={field.onChange}
                    value={field.value ?? ""}
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
                          !repositories.some(
                            (repository) => repository.git_url === field.value
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

          <Button type="submit" disabled={isUpdating} size="sm">
            {isUpdating ? "Saving..." : "Save"}
          </Button>
        </form>
      </Form>

      {persistedGitUrl && (
        <div className="rounded-lg border bg-muted/30 p-4">
          <div className="mb-4 flex items-center justify-between">
            <div>
              <h5 className="text-sm font-medium">Workflow synchronization</h5>
              <p className="text-xs text-muted-foreground">
                Pull workflow definitions from your Git repository into this
                workspace
              </p>
            </div>
            <Button
              type="button"
              variant="outline"
              size="sm"
              onClick={() => setPullDialogOpen(true)}
              className="flex items-center gap-2"
            >
              <GitPullRequestIcon className="size-4" />
              <span>Pull workflows</span>
            </Button>
          </div>

          <div className="text-xs text-muted-foreground">
            <p>• Select a commit SHA to pull specific workflow versions</p>
            <p>
              • All changes are atomic - either all workflows import or none do
            </p>
          </div>
        </div>
      )}

      <WorkflowPullDialog
        open={pullDialogOpen}
        onOpenChange={setPullDialogOpen}
        workspaceId={workspace.id}
        gitRepoUrl={persistedGitUrl}
        onPullSuccess={() => {
          console.log("Workflows pulled successfully")
        }}
      />
    </div>
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
