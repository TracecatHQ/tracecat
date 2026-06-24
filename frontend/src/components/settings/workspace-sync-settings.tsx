"use client"

import { zodResolver } from "@hookform/resolvers/zod"
import {
  AlertTriangleIcon,
  ArrowDownIcon,
  ArrowUpIcon,
  CheckCircle2Icon,
  GitBranchIcon,
  GitPullRequestIcon,
  Loader2Icon,
  PencilIcon,
  SearchIcon,
  XCircleIcon,
} from "lucide-react"
import { useEffect, useState } from "react"
import { useForm, useWatch } from "react-hook-form"
import { z } from "zod"
import type { GitHubAppRepository, PullResult, WorkspaceRead } from "@/client"
import { CommitSelector } from "@/components/registry/commit-selector"
import { Badge } from "@/components/ui/badge"
import { Button } from "@/components/ui/button"
import { Checkbox } from "@/components/ui/checkbox"
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
import { Label } from "@/components/ui/label"
import {
  Select,
  SelectContent,
  SelectGroup,
  SelectItem,
  SelectTrigger,
  SelectValue,
} from "@/components/ui/select"
import { Skeleton } from "@/components/ui/skeleton"
import { Tabs, TabsContent, TabsList, TabsTrigger } from "@/components/ui/tabs"
import { ToggleTabs } from "@/components/ui/toggle-tabs"
import { toast } from "@/components/ui/use-toast"
import {
  useWorkspaceSyncBranchTarget,
  WorkspaceSyncBranchSelector,
} from "@/components/workspace-sync/branch-target-selector"
import { PullResourceManifest } from "@/components/workspace-sync/push-resource-manifest"
import {
  getWorkspaceSyncPushButtonLabel,
  getWorkspaceSyncPushOutcome,
  getWorkspaceSyncPushResultLabel,
  getWorkspaceSyncPushWarning,
  type WorkspaceSyncPushMode,
  WorkspaceSyncPushModeTabs,
} from "@/components/workspace-sync/push-target-policy"
import {
  PushResourcePreview,
  ResourceDiffSection,
} from "@/components/workspace-sync/resource-diff-review"
import {
  getWorkspaceSyncResourceLabel,
  workspaceSyncResourceCountEntries,
} from "@/components/workspace-sync/resource-metadata"
import {
  useRepositoryBranches,
  useRepositoryCommits,
  useWorkflowSync,
  useWorkspaceSyncExport,
  useWorkspaceSyncExportPreview,
} from "@/hooks/use-workspace-sync"
import { getApiErrorDetail } from "@/lib/errors"
import { getRelativeTime } from "@/lib/event-history"
import { getRepoDisplayName, validateGitSshUrl } from "@/lib/git"
import { useGitHubAppRepositories, useWorkspaceSettings } from "@/lib/hooks"
import { cn } from "@/lib/utils"

type SyncMode = "push" | "pull"
type RepositoryInputMode = "select" | "manual"

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

/**
 * Git sync settings panel for a workspace.
 *
 * Presents a single connection row plus a Push / Pull switch so both sync
 * directions live in one place. Push opens or reuses a pull request for the
 * selected branch; pull imports a selected commit back into the workspace.
 */
export function WorkspaceSyncSettings({
  workspace,
}: WorkspaceSyncSettingsProps) {
  const persistedGitUrl = workspace.settings?.git_repo_url || undefined
  const { updateWorkspace, isUpdating } = useWorkspaceSettings(workspace.id)
  const { exportWorkspace, exportWorkspaceIsPending } = useWorkspaceSyncExport(
    workspace.id
  )
  const { pullWorkflows, pullWorkflowsIsPending } = useWorkflowSync(
    workspace.id
  )
  const {
    repositories = [],
    repositoriesIsLoading,
    repositoriesError,
  } = useGitHubAppRepositories(workspace.id)

  const [isEditingConnection, setIsEditingConnection] = useState(false)
  const [mode, setMode] = useState<SyncMode>("push")

  // Push composer state
  const [exportMessage, setExportMessage] = useState("Export workspace config")
  const [exportPreviewRequested, setExportPreviewRequested] = useState(false)
  const [pushMode, setPushMode] =
    useState<WorkspaceSyncPushMode>("pull-request")

  // Pull composer state
  const [selectedCommitSha, setSelectedCommitSha] = useState<string | null>(
    null
  )
  const [syncSchedules, setSyncSchedules] = useState(false)
  const [pullPreview, setPullPreview] = useState<PullResult | null>(null)
  const [pullPreviewOptions, setPullPreviewOptions] = useState<{
    commitSha: string
    syncSchedules: boolean
  } | null>(null)
  const [pullResult, setPullResult] = useState<PullResult | null>(null)
  const [pullAction, setPullAction] = useState<"preview" | "apply" | null>(null)

  const {
    branches: repoBranches,
    branchesIsLoading,
    branchesError,
  } = useRepositoryBranches(workspace.id, {
    enabled: Boolean(persistedGitUrl),
    limit: 200,
  })

  const {
    branch: exportBranch,
    setBranch: setExportBranch,
    isCreatingBranch,
    selectBranch: selectExportBranch,
    defaultBranch,
    hasBranches,
  } = useWorkspaceSyncBranchTarget({
    branches: repoBranches,
    newBranchPrefix: "sync/workspace",
  })

  const { commits, commitsIsLoading, commitsError } = useRepositoryCommits(
    workspace.id,
    {
      branch: defaultBranch,
      limit: 20,
      enabled: Boolean(persistedGitUrl) && Boolean(defaultBranch),
    }
  )
  const repoDisplayName = getRepoDisplayName(persistedGitUrl)
  const latestCommit = commits?.[0]
  const targetBranch = exportBranch.trim()
  const exportCompareRef = isCreatingBranch
    ? defaultBranch
    : targetBranch || undefined
  const {
    preview: exportPreview,
    previewIsLoading: exportPreviewIsLoading,
    previewError: exportPreviewError,
    refetchPreview: refetchExportPreview,
  } = useWorkspaceSyncExportPreview(workspace.id, {
    compareRef: exportCompareRef,
    enabled: false,
  })
  const visibleExportPreview = exportPreviewRequested
    ? exportPreview
    : undefined
  const visibleExportPreviewIsLoading =
    exportPreviewRequested && exportPreviewIsLoading
  const exportPreviewErrorMessage = exportPreviewError
    ? (getApiErrorDetail(exportPreviewError) ?? "Request failed")
    : undefined
  const pushOutcome = getWorkspaceSyncPushOutcome({
    mode: pushMode,
    targetBranch,
    defaultBranch,
    isCreatingBranch,
  })
  const pushWarning = getWorkspaceSyncPushWarning({
    outcome: pushOutcome,
    defaultBranch,
  })
  const exportDisabled =
    exportWorkspaceIsPending ||
    branchesIsLoading ||
    (!hasBranches && !isCreatingBranch) ||
    pushOutcome.isPullRequestBlocked ||
    targetBranch === "" ||
    exportMessage.trim() === ""
  const effectivePullSha = selectedCommitSha ?? commits?.[0]?.sha
  const pullPreviewMatchesSelection =
    Boolean(effectivePullSha) &&
    pullPreviewOptions !== null &&
    pullPreviewOptions?.commitSha === effectivePullSha &&
    pullPreviewOptions.syncSchedules === syncSchedules
  const canApplyPull =
    pullPreviewMatchesSelection && pullPreview?.success === true

  const showConnectionForm = !persistedGitUrl || isEditingConnection

  const form = useForm<SyncSettingsForm>({
    resolver: zodResolver(syncSettingsSchema),
    mode: "onChange",
    defaultValues: {
      git_repo_url: workspace.settings?.git_repo_url || "",
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

  // Default the pull source to HEAD once commits load.
  useEffect(() => {
    if (commits?.length && !selectedCommitSha) {
      setSelectedCommitSha(commits[0].sha)
    }
  }, [commits, selectedCommitSha])

  useEffect(() => {
    setPullPreview(null)
    setPullPreviewOptions(null)
    setPullResult(null)
  }, [effectivePullSha, syncSchedules])

  useEffect(() => {
    setExportPreviewRequested(false)
  }, [exportCompareRef, persistedGitUrl])

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
    setIsEditingConnection(false)
  }

  function handleEditConnection() {
    form.reset({ git_repo_url: persistedGitUrl ?? "" })
    setIsEditingConnection(true)
  }

  function handleCancelEdit() {
    form.reset({ git_repo_url: persistedGitUrl ?? "" })
    setIsEditingConnection(false)
  }

  async function onExport() {
    try {
      const result = await exportWorkspace({
        message: exportMessage,
        branch: targetBranch,
        create_pr: pushOutcome.createPr,
        include_schedules: false,
        provider: "github",
      })
      toast({
        title: result.commit.pr_url
          ? "Pull request ready"
          : "Workspace config pushed",
        description:
          result.commit.pr_url ?? result.commit.sha ?? result.commit.message,
      })
    } catch (error) {
      toast({
        title: "Push failed",
        description: getApiErrorDetail(error) ?? "Request failed",
        variant: "destructive",
      })
    }
  }

  function handlePreviewExport() {
    if (!exportCompareRef) {
      return
    }
    setExportPreviewRequested(true)
    void refetchExportPreview()
  }

  async function handlePreviewPull() {
    if (!effectivePullSha) {
      return
    }

    setPullAction("preview")
    setPullPreview(null)
    setPullResult(null)
    try {
      const result = await pullWorkflows({
        commit_sha: effectivePullSha,
        dry_run: true,
        sync_schedules: syncSchedules,
      })
      setPullPreview(result)
      setPullPreviewOptions({
        commitSha: effectivePullSha,
        syncSchedules,
      })
      toast({
        title: result.success ? "Pull preview ready" : "Pull preview failed",
        description: result.message,
        variant: result.success ? undefined : "destructive",
      })
    } catch (error) {
      toast({
        title: "Pull preview failed",
        description: getApiErrorDetail(error) ?? "Request failed",
        variant: "destructive",
      })
    } finally {
      setPullAction(null)
    }
  }

  async function handleApplyPull() {
    if (!effectivePullSha || !canApplyPull) {
      return
    }

    setPullAction("apply")
    setPullResult(null)
    try {
      const result = await pullWorkflows({
        commit_sha: effectivePullSha,
        sync_schedules: syncSchedules,
      })
      setPullResult(result)
      if (result.success) {
        setPullPreview(null)
        setPullPreviewOptions(null)
      }
      toast({
        title: result.success
          ? "Workspace pull completed"
          : "Workspace pull failed",
        description: result.message,
        variant: result.success ? undefined : "destructive",
      })
    } catch (error) {
      toast({
        title: "Pull operation failed",
        description: getApiErrorDetail(error) ?? "Request failed",
        variant: "destructive",
      })
    } finally {
      setPullAction(null)
    }
  }

  return (
    <div className="space-y-6">
      {showConnectionForm ? (
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
                      value={getRepositorySelectValue(
                        field.value,
                        repositories
                      )}
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
                <Button
                  type="button"
                  variant="ghost"
                  size="sm"
                  onClick={handleCancelEdit}
                >
                  Cancel
                </Button>
              )}
            </div>
          </form>
        </Form>
      ) : (
        <div className="flex items-center justify-between gap-3 rounded-xl border bg-card px-4 py-3">
          <div className="flex min-w-0 items-center gap-3">
            <div className="flex size-9 shrink-0 items-center justify-center rounded-lg bg-muted">
              <GitBranchIcon className="size-4 text-muted-foreground" />
            </div>
            <div className="min-w-0 space-y-1">
              <p className="truncate font-mono text-sm font-semibold">
                {repoDisplayName ?? persistedGitUrl}
              </p>
              <ConnectionStatus
                branchesIsLoading={branchesIsLoading}
                hasBranchesError={Boolean(branchesError)}
                defaultBranch={defaultBranch}
                commitsIsLoading={commitsIsLoading}
                latestCommitSha={latestCommit?.sha}
                latestCommitDate={latestCommit?.date}
              />
            </div>
          </div>
          <Button
            type="button"
            variant="ghost"
            size="sm"
            className="shrink-0 gap-1.5 text-muted-foreground hover:text-foreground"
            onClick={handleEditConnection}
          >
            <PencilIcon className="size-3.5" />
            Edit connection
          </Button>
        </div>
      )}

      {persistedGitUrl && !isEditingConnection && (
        <Tabs
          value={mode}
          onValueChange={(value) => setMode(value as SyncMode)}
          className="space-y-5"
        >
          <TabsList>
            <TabsTrigger value="push" disableUnderline className="gap-1.5">
              <ArrowUpIcon className="size-3.5" />
              Push
            </TabsTrigger>
            <TabsTrigger value="pull" disableUnderline className="gap-1.5">
              <ArrowDownIcon className="size-3.5" />
              Pull
            </TabsTrigger>
          </TabsList>

          <TabsContent value="push" className="space-y-4">
            <div className="space-y-2">
              <Label htmlFor="workspace-sync-message">Commit message</Label>
              <Input
                id="workspace-sync-message"
                value={exportMessage}
                onChange={(event) => setExportMessage(event.target.value)}
              />
            </div>

            <div className="space-y-2">
              <p className="text-sm font-medium">Push mode</p>
              <WorkspaceSyncPushModeTabs
                value={pushMode}
                onValueChange={setPushMode}
              />
            </div>

            <div className="space-y-2">
              <Label htmlFor="workspace-sync-branch">Branch</Label>
              <WorkspaceSyncBranchSelector
                id="workspace-sync-branch"
                branches={repoBranches}
                branch={exportBranch}
                isCreatingBranch={isCreatingBranch}
                branchesIsLoading={branchesIsLoading}
                hasBranches={hasBranches}
                branchesError={branchesError}
                newBranchPlaceholder="sync/workspace"
                onSelectBranch={selectExportBranch}
                onBranchChange={setExportBranch}
              />
              {pushWarning ? (
                <p
                  className={
                    pushOutcome.isPullRequestBlocked
                      ? "text-[11px] text-destructive"
                      : "text-[11px] text-amber-700"
                  }
                >
                  {pushWarning}
                </p>
              ) : null}
            </div>

            <PushResourcePreview
              preview={visibleExportPreview}
              isLoading={visibleExportPreviewIsLoading}
              compareRef={exportCompareRef}
              errorMessage={
                exportPreviewRequested ? exportPreviewErrorMessage : undefined
              }
              hasRequestedPreview={exportPreviewRequested}
              onRequestPreview={handlePreviewExport}
            />

            <div className="flex flex-col gap-3 border-t pt-4 sm:flex-row sm:items-center sm:justify-between">
              <div className="flex flex-wrap items-center gap-1.5 rounded-md bg-muted px-3 py-2 text-xs text-muted-foreground">
                <ArrowUpIcon className="size-3.5" />
                <span>Pushing to</span>
                <span className="font-mono text-foreground">
                  {repoDisplayName ?? "repository"}
                </span>
                <span>@</span>
                <span className="font-mono text-foreground">
                  {targetBranch || "—"}
                </span>
                <span>·</span>
                <span className="text-foreground">
                  {getWorkspaceSyncPushResultLabel({
                    outcome: pushOutcome,
                    defaultBranch,
                  })}
                </span>
              </div>
              <Button
                type="button"
                size="sm"
                onClick={onExport}
                disabled={exportDisabled}
                className="shrink-0 gap-1.5"
              >
                {exportWorkspaceIsPending ? (
                  <Loader2Icon className="size-4 animate-spin" />
                ) : pushOutcome.createPr ? (
                  <GitPullRequestIcon className="size-4" />
                ) : (
                  <ArrowUpIcon className="size-4" />
                )}
                {getWorkspaceSyncPushButtonLabel({
                  outcome: pushOutcome,
                  isCreatingBranch,
                  isPending: exportWorkspaceIsPending,
                })}
              </Button>
            </div>
          </TabsContent>

          <TabsContent value="pull" className="space-y-4">
            <div className="space-y-2">
              <div className="flex flex-wrap items-center gap-3">
                <Label className="shrink-0">Pull from commit</Label>
                <CommitSelector
                  commits={commits}
                  currentCommitSha={selectedCommitSha}
                  isLoading={commitsIsLoading}
                  error={commitsError}
                  onSelectCommit={setSelectedCommitSha}
                  disabled={pullWorkflowsIsPending}
                />
              </div>
              <p className="text-[11px] text-muted-foreground">
                Choosing an older commit rolls the workspace back to a
                known-good snapshot.
              </p>
            </div>

            <label className="flex items-center gap-2 text-sm">
              <Checkbox
                checked={syncSchedules}
                onCheckedChange={(checked) =>
                  setSyncSchedules(checked === true)
                }
                disabled={pullWorkflowsIsPending}
              />
              Overwrite schedules
            </label>

            <SyncWarning>
              Preview the incoming resource diff before applying. Existing
              resources with the same ID will be overwritten. Schedules are
              preserved unless checked above.
            </SyncWarning>

            {pullPreview && pullPreviewMatchesSelection && (
              <PullPreviewSummary result={pullPreview} />
            )}
            {pullResult && <PullResultSummary result={pullResult} />}
            {!(pullPreview && pullPreviewMatchesSelection) && !pullResult && (
              <PullEmptyState />
            )}

            <div className="flex flex-col gap-3 border-t pt-4 sm:flex-row sm:items-center sm:justify-between">
              <div className="flex flex-wrap items-center gap-1.5 rounded-md bg-muted px-3 py-2 text-xs text-muted-foreground">
                <ArrowDownIcon className="size-3.5" />
                <span>Importing into this workspace from</span>
                <span className="font-mono text-foreground">
                  {effectivePullSha?.substring(0, 7) ?? "—"}
                </span>
              </div>
              <div className="flex items-center gap-2">
                <Button
                  type="button"
                  size="sm"
                  variant="outline"
                  onClick={handlePreviewPull}
                  disabled={
                    pullWorkflowsIsPending ||
                    commitsIsLoading ||
                    !effectivePullSha
                  }
                  className="shrink-0 gap-1.5"
                >
                  {pullWorkflowsIsPending && pullAction === "preview" ? (
                    <Loader2Icon className="size-4 animate-spin" />
                  ) : (
                    <SearchIcon className="size-4" />
                  )}
                  {pullWorkflowsIsPending && pullAction === "preview"
                    ? "Previewing..."
                    : "Preview changes"}
                </Button>
                <Button
                  type="button"
                  size="sm"
                  onClick={handleApplyPull}
                  disabled={pullWorkflowsIsPending || !canApplyPull}
                  className="shrink-0 gap-1.5"
                >
                  {pullWorkflowsIsPending && pullAction === "apply" ? (
                    <Loader2Icon className="size-4 animate-spin" />
                  ) : (
                    <ArrowDownIcon className="size-4" />
                  )}
                  {pullWorkflowsIsPending && pullAction === "apply"
                    ? "Applying..."
                    : "Apply pull"}
                </Button>
              </div>
            </div>
          </TabsContent>
        </Tabs>
      )}
    </div>
  )
}

interface ConnectionStatusProps {
  branchesIsLoading: boolean
  hasBranchesError: boolean
  defaultBranch: string | undefined
  commitsIsLoading: boolean
  latestCommitSha: string | undefined
  latestCommitDate: string | undefined
}

/**
 * Compact, truthful connection status line: connection health, the default
 * branch, and the latest remote commit. States facts about the remote only.
 */
function ConnectionStatus({
  branchesIsLoading,
  hasBranchesError,
  defaultBranch,
  commitsIsLoading,
  latestCommitSha,
  latestCommitDate,
}: ConnectionStatusProps) {
  if (branchesIsLoading) {
    return (
      <p className="text-xs text-muted-foreground">Checking connection...</p>
    )
  }

  if (hasBranchesError) {
    return (
      <p className="flex items-center gap-1.5 text-xs text-destructive">
        <AlertTriangleIcon className="size-3.5" />
        Could not reach repository
      </p>
    )
  }

  return (
    <div className="flex flex-wrap items-center gap-x-2 gap-y-1 text-xs text-muted-foreground">
      <span className="inline-flex items-center gap-1.5 rounded-md border border-green-200 bg-green-50 px-2 py-0.5 text-[11px] font-medium text-green-700">
        <span className="size-1.5 rounded-full bg-green-500" />
        Connected
      </span>
      {defaultBranch && (
        <>
          <span>·</span>
          <span className="font-mono font-medium text-foreground">
            {defaultBranch}
          </span>
        </>
      )}
      {commitsIsLoading ? (
        <Skeleton className="h-3 w-28 rounded-sm" />
      ) : latestCommitSha ? (
        <>
          <span>· latest</span>
          <span className="font-mono text-foreground">
            {latestCommitSha.substring(0, 7)}
          </span>
          {latestCommitDate && (
            <span>· {getRelativeTime(new Date(latestCommitDate))}</span>
          )}
        </>
      ) : (
        <span>· no commits yet</span>
      )}
    </div>
  )
}

/**
 * Inline amber advisory used for pull overwrite consequences.
 */
function SyncWarning({ children }: { children: React.ReactNode }) {
  return (
    <div className="flex items-start gap-2 rounded-md border border-amber-200 bg-amber-50 px-3 py-2 text-[11px] text-amber-700">
      <AlertTriangleIcon className="mt-0.5 size-3.5 shrink-0" />
      <span>{children}</span>
    </div>
  )
}

/**
 * Placeholder shown in the pull view before a preview has been generated,
 * nudging the user to preview the incoming diff before applying.
 */
function PullEmptyState() {
  return (
    <div className="flex flex-col items-center gap-2 rounded-md border border-dashed px-6 py-10 text-center">
      <SearchIcon className="size-5 text-muted-foreground" />
      <p className="text-sm font-medium text-foreground">No preview yet</p>
      <p className="max-w-xs text-xs text-muted-foreground">
        Preview changes first to review the incoming resource diff before
        applying the pull.
      </p>
    </div>
  )
}

/**
 * Dry-run pull preview: a compact summary line plus a reviewable list of
 * per-resource file diffs.
 */
function PullPreviewSummary({ result }: { result: PullResult }) {
  const resourceCounts = workspaceSyncResourceCountEntries(result)
  const totalFound =
    resourceCounts.length > 0
      ? resourceCounts.reduce((total, [, count]) => total + count.found, 0)
      : (result.workflows_found ?? 0)
  const resourceDiffs = result.resource_diffs ?? []
  const addedCount = resourceDiffs.filter(
    (diff) => diff.change_type === "added"
  ).length
  const modifiedCount = resourceDiffs.filter(
    (diff) => diff.change_type === "modified"
  ).length

  return (
    <div className="space-y-3">
      <div className="flex flex-wrap items-center gap-x-3 gap-y-2">
        <span className="flex items-center gap-1.5 text-sm font-medium">
          {result.success ? (
            <CheckCircle2Icon className="size-4 text-green-600" />
          ) : (
            <XCircleIcon className="size-4 text-destructive" />
          )}
          {result.success ? "Pull preview" : "Preview failed"}
        </span>
        <span className="h-4 w-px bg-border" />
        <SummaryMetric label="found" value={totalFound} />
        <SummaryMetric
          label="changes"
          value={resourceDiffs.length}
          emphasize={resourceDiffs.length > 0}
        />
        <SummaryMetric
          label="issues"
          value={result.diagnostics.length}
          emphasize={result.diagnostics.length > 0}
        />
        <div className="ml-auto flex flex-wrap gap-1.5">
          <Badge variant="secondary" className="font-normal">
            {addedCount} added
          </Badge>
          <Badge variant="secondary" className="font-normal">
            {modifiedCount} modified
          </Badge>
        </div>
      </div>

      {!result.success && (
        <p className="text-sm text-muted-foreground">{result.message}</p>
      )}

      <PullResourceManifest result={result} />

      <ResourceDiffSection diffs={resourceDiffs} />

      {result.diagnostics.length > 0 && (
        <PullDiagnostics diagnostics={result.diagnostics} />
      )}
    </div>
  )
}

/**
 * Single inline metric ("13 found") for the pull preview summary line.
 */
function SummaryMetric({
  label,
  value,
  emphasize = false,
}: {
  label: string
  value: number
  emphasize?: boolean
}) {
  return (
    <span className="text-sm tabular-nums">
      <span className={cn("font-medium", emphasize && "text-amber-600")}>
        {value}
      </span>{" "}
      <span className="text-muted-foreground">{label}</span>
    </span>
  )
}

/**
 * Shared diagnostic list for pull previews and completed pulls.
 */
function PullDiagnostics({
  diagnostics,
}: {
  diagnostics: PullResult["diagnostics"]
}) {
  return (
    <div className="space-y-2">
      <h6 className="text-sm font-medium">Issues found:</h6>
      <div className="max-h-32 space-y-2 overflow-y-auto">
        {diagnostics.map((diagnostic) => (
          <div
            key={diagnostic.workflow_title || diagnostic.workflow_path}
            className="flex items-start gap-2 rounded bg-muted p-2 text-xs"
          >
            <AlertTriangleIcon className="mt-0.5 size-3 shrink-0 text-amber-500" />
            <div className="min-w-0 space-y-1">
              <div className="font-medium">
                {diagnostic.workflow_title || diagnostic.workflow_path}
              </div>
              <div className="text-muted-foreground">{diagnostic.message}</div>
              <Badge variant="outline" className="text-xs">
                {diagnostic.error_type}
              </Badge>
            </div>
          </div>
        ))}
      </div>
    </div>
  )
}

/**
 * Compact summary of a completed pull: counts, per-resource breakdown, and any
 * diagnostics.
 */
function PullResultSummary({ result }: { result: PullResult }) {
  const resourceCounts = workspaceSyncResourceCountEntries(result)
  const totalFound =
    resourceCounts.length > 0
      ? resourceCounts.reduce((total, [, count]) => total + count.found, 0)
      : (result.workflows_found ?? 0)
  const totalImported =
    resourceCounts.length > 0
      ? resourceCounts.reduce((total, [, count]) => total + count.imported, 0)
      : (result.workflows_imported ?? 0)

  return (
    <div className="space-y-3 rounded-lg border p-4">
      <div className="flex items-center gap-2">
        {result.success ? (
          <CheckCircle2Icon className="size-4 text-green-600" />
        ) : (
          <XCircleIcon className="size-4 text-destructive" />
        )}
        <h5 className="text-sm font-medium">
          {result.success ? "Pull completed" : "Pull failed"}
        </h5>
      </div>

      <div className="grid grid-cols-3 gap-4 text-sm">
        <div>
          <span className="text-muted-foreground">Found:</span>
          <span className="ml-1 font-medium">{totalFound}</span>
        </div>
        <div>
          <span className="text-muted-foreground">Imported:</span>
          <span className="ml-1 font-medium text-green-600">
            {totalImported}
          </span>
        </div>
        <div>
          <span className="text-muted-foreground">Issues:</span>
          <span className="ml-1 font-medium text-amber-600">
            {result.diagnostics.length}
          </span>
        </div>
      </div>

      {resourceCounts.length > 0 && (
        <div className="grid grid-cols-2 gap-2 text-xs sm:grid-cols-3">
          {resourceCounts.map(([resourceType, count]) => (
            <div
              key={resourceType}
              className="rounded-md border bg-muted/30 px-2 py-1.5"
            >
              <div className="font-medium">
                {getWorkspaceSyncResourceLabel(resourceType)}
              </div>
              <div className="text-muted-foreground">
                {count.imported}/{count.found}
              </div>
            </div>
          ))}
        </div>
      )}

      <p className="text-sm">{result.message}</p>

      {result.diagnostics.length > 0 && (
        <PullDiagnostics diagnostics={result.diagnostics} />
      )}
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
