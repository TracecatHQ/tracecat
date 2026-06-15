"use client"

import { zodResolver } from "@hookform/resolvers/zod"
import {
  AlertTriangleIcon,
  ArrowDownIcon,
  ArrowUpIcon,
  CheckCircle2Icon,
  GitBranchIcon,
  GitCommitHorizontalIcon,
  GitPullRequestIcon,
  Loader2Icon,
  PencilIcon,
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
  SelectSeparator,
  SelectTrigger,
  SelectValue,
} from "@/components/ui/select"
import { Skeleton } from "@/components/ui/skeleton"
import { Tabs, TabsContent, TabsList, TabsTrigger } from "@/components/ui/tabs"
import { ToggleTabs } from "@/components/ui/toggle-tabs"
import { toast } from "@/components/ui/use-toast"
import {
  useRepositoryBranches,
  useRepositoryCommits,
  useWorkflowSync,
  useWorkspaceSyncExport,
} from "@/hooks/use-workspace-sync"
import { getApiErrorDetail } from "@/lib/errors"
import { getRelativeTime } from "@/lib/event-history"
import { getRepoDisplayName, validateGitSshUrl } from "@/lib/git"
import { useGitHubAppRepositories, useWorkspaceSettings } from "@/lib/hooks"
import { cn } from "@/lib/utils"

const CREATE_NEW_BRANCH_VALUE = "__create_new_branch__"

type SyncMode = "push" | "pull"
type DeliveryMode = "direct" | "pr"
type RepositoryInputMode = "select" | "manual"

const RESOURCE_LABELS: Record<string, string> = {
  workflow: "Workflows",
  agent_preset: "Agent presets",
  skill: "Skills",
  table: "Tables",
  case_tag: "Case tags",
  case_field: "Case fields",
  case_dropdown: "Case dropdowns",
  case_duration: "Case durations",
  variable: "Variables",
  secret_metadata: "Secret metadata",
}

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

function resourceCountEntries(result: PullResult) {
  return Object.entries(result.resource_counts ?? {})
    .filter(([, count]) => count.found > 0 || count.imported > 0)
    .sort(([left], [right]) => left.localeCompare(right))
}

/**
 * Git sync settings panel for a workspace.
 *
 * Presents a single connection row plus a Push / Pull switch so both sync
 * directions live in one place. Push composes a commit (message, branch, and an
 * explicit "commit directly" vs "open a pull request" choice); pull imports a
 * selected commit back into the workspace.
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
  const [exportBranch, setExportBranch] = useState("")
  const [exportMessage, setExportMessage] = useState("Export workspace config")
  const [delivery, setDelivery] = useState<DeliveryMode>("direct")
  const [isCreatingBranch, setIsCreatingBranch] = useState(false)

  // Pull composer state
  const [selectedCommitSha, setSelectedCommitSha] = useState<string | null>(
    null
  )
  const [syncSchedules, setSyncSchedules] = useState(false)
  const [pullResult, setPullResult] = useState<PullResult | null>(null)

  const {
    branches: repoBranches,
    branchesIsLoading,
    branchesError,
  } = useRepositoryBranches(workspace.id, {
    enabled: Boolean(persistedGitUrl),
    limit: 200,
  })

  const defaultBranch =
    repoBranches?.find((branch) => branch.is_default)?.name ??
    repoBranches?.[0]?.name

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
  const hasBranches = (repoBranches?.length ?? 0) > 0
  const selectedBranchInfo = repoBranches?.find(
    (branch) => branch.name === exportBranch
  )
  const isDefaultBranchSelected = selectedBranchInfo?.is_default ?? false
  const createPr = delivery === "pr"
  const exportDisabled =
    exportWorkspaceIsPending ||
    branchesIsLoading ||
    (!hasBranches && !isCreatingBranch) ||
    exportBranch.trim() === "" ||
    exportMessage.trim() === ""
  const effectivePullSha = selectedCommitSha ?? commits?.[0]?.sha

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

  // Default the push target to the repository default branch.
  useEffect(() => {
    if (
      !persistedGitUrl ||
      !repoBranches ||
      repoBranches.length === 0 ||
      isCreatingBranch
    ) {
      return
    }

    const branchNames = new Set(repoBranches.map((branch) => branch.name))
    if (exportBranch && branchNames.has(exportBranch)) {
      return
    }

    if (defaultBranch) {
      setExportBranch(defaultBranch)
    }
  }, [
    exportBranch,
    isCreatingBranch,
    persistedGitUrl,
    repoBranches,
    defaultBranch,
  ])

  // Default the pull source to HEAD once commits load.
  useEffect(() => {
    if (commits?.length && !selectedCommitSha) {
      setSelectedCommitSha(commits[0].sha)
    }
  }, [commits, selectedCommitSha])

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
        branch: exportBranch,
        create_pr: createPr,
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

  async function handlePull() {
    if (!effectivePullSha) {
      return
    }

    setPullResult(null)
    try {
      const result = await pullWorkflows({
        commit_sha: effectivePullSha,
        sync_schedules: syncSchedules,
      })
      setPullResult(result)
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
          <div className="flex flex-wrap items-center justify-between gap-3">
            <TabsList>
              <TabsTrigger value="push" disableUnderline className="gap-1.5">
                <ArrowUpIcon className="size-3.5" />
                Push to Git
              </TabsTrigger>
              <TabsTrigger value="pull" disableUnderline className="gap-1.5">
                <ArrowDownIcon className="size-3.5" />
                Pull from Git
              </TabsTrigger>
            </TabsList>
            <Badge variant="secondary" className="font-normal">
              Workspace config
            </Badge>
          </div>

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
              <Label htmlFor="workspace-sync-branch">Branch</Label>
              <Select
                value={
                  isCreatingBranch ||
                  !repoBranches?.some((branch) => branch.name === exportBranch)
                    ? CREATE_NEW_BRANCH_VALUE
                    : exportBranch
                }
                onValueChange={(value) => {
                  if (value === CREATE_NEW_BRANCH_VALUE) {
                    setIsCreatingBranch(true)
                    setExportBranch("sync/workspace")
                    return
                  }
                  setIsCreatingBranch(false)
                  setExportBranch(value)
                }}
                disabled={branchesIsLoading || !hasBranches}
              >
                <SelectTrigger id="workspace-sync-branch" className="min-w-0">
                  {branchesIsLoading ? (
                    <Skeleton className="h-4 w-full rounded-sm" />
                  ) : (
                    <SelectValue placeholder="Select branch" />
                  )}
                </SelectTrigger>
                <SelectContent>
                  {hasBranches ? (
                    <>
                      <SelectItem value={CREATE_NEW_BRANCH_VALUE}>
                        Create new branch...
                      </SelectItem>
                      <SelectSeparator />
                      {(repoBranches ?? []).map((branch) => (
                        <SelectItem key={branch.name} value={branch.name}>
                          <div className="flex items-center gap-2">
                            <span>{branch.name}</span>
                            {branch.is_default && (
                              <Badge
                                variant="secondary"
                                className="h-4 rounded-sm px-1 text-[10px] font-normal"
                              >
                                default
                              </Badge>
                            )}
                          </div>
                        </SelectItem>
                      ))}
                    </>
                  ) : (
                    <SelectItem value="__no_branches" disabled>
                      No branches found
                    </SelectItem>
                  )}
                </SelectContent>
              </Select>
              {isCreatingBranch && (
                <div className="space-y-1">
                  <Input
                    value={exportBranch}
                    onChange={(event) => setExportBranch(event.target.value)}
                    placeholder="sync/workspace"
                  />
                  <p className="text-[11px] text-muted-foreground">
                    The branch will be created from the repository default
                    branch.
                  </p>
                </div>
              )}
              {!branchesIsLoading && !hasBranches && (
                <p className="text-[11px] text-muted-foreground">
                  No branches available from the configured repository.
                </p>
              )}
              {branchesError && (
                <p className="text-[11px] text-destructive">
                  Failed to load repository branches.
                </p>
              )}
            </div>

            <div className="space-y-2">
              <Label>How to deliver</Label>
              <div className="grid grid-cols-1 gap-2 sm:grid-cols-2">
                <DeliveryOption
                  selected={delivery === "direct"}
                  onClick={() => setDelivery("direct")}
                  icon={<GitCommitHorizontalIcon className="size-4" />}
                  title="Commit directly"
                  description={`Writes straight onto ${exportBranch || "the branch"}. No review step.`}
                />
                <DeliveryOption
                  selected={delivery === "pr"}
                  onClick={() => setDelivery("pr")}
                  icon={<GitPullRequestIcon className="size-4" />}
                  title="Open a pull request"
                  description="Pushes to a branch and reuses an open PR if one exists."
                />
              </div>
            </div>

            {delivery === "direct" && isDefaultBranchSelected && (
              <SyncWarning>
                This commits directly to the default branch — changes go live
                immediately.
              </SyncWarning>
            )}

            <div className="flex flex-col gap-3 border-t pt-4 sm:flex-row sm:items-center sm:justify-between">
              <div className="flex flex-wrap items-center gap-1.5 rounded-md bg-muted px-3 py-2 text-xs text-muted-foreground">
                <ArrowUpIcon className="size-3.5" />
                <span>Pushing to</span>
                <span className="font-mono text-foreground">
                  {repoDisplayName ?? "repository"}
                </span>
                <span>@</span>
                <span className="font-mono text-foreground">
                  {exportBranch || "—"}
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
                ) : (
                  <ArrowUpIcon className="size-4" />
                )}
                {exportWorkspaceIsPending ? "Pushing..." : "Push changes"}
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
              Also update schedules
            </label>

            <SyncWarning>
              Existing resources with the same ID will be overwritten. Schedules
              are preserved unless checked above.
            </SyncWarning>

            {pullResult && <PullResultSummary result={pullResult} />}

            <div className="flex flex-col gap-3 border-t pt-4 sm:flex-row sm:items-center sm:justify-between">
              <div className="flex flex-wrap items-center gap-1.5 rounded-md bg-muted px-3 py-2 text-xs text-muted-foreground">
                <ArrowDownIcon className="size-3.5" />
                <span>Importing into this workspace from</span>
                <span className="font-mono text-foreground">
                  {effectivePullSha?.substring(0, 7) ?? "—"}
                </span>
              </div>
              <Button
                type="button"
                size="sm"
                onClick={handlePull}
                disabled={
                  pullWorkflowsIsPending ||
                  commitsIsLoading ||
                  !effectivePullSha
                }
                className="shrink-0 gap-1.5"
              >
                {pullWorkflowsIsPending ? (
                  <Loader2Icon className="size-4 animate-spin" />
                ) : (
                  <ArrowDownIcon className="size-4" />
                )}
                {pullWorkflowsIsPending ? "Pulling..." : "Pull workspace"}
              </Button>
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

interface DeliveryOptionProps {
  selected: boolean
  onClick: () => void
  icon: React.ReactNode
  title: string
  description: string
}

/**
 * A selectable card used to choose how a push is delivered (direct commit vs
 * pull request). Spells out the consequence of each choice inline.
 */
function DeliveryOption({
  selected,
  onClick,
  icon,
  title,
  description,
}: DeliveryOptionProps) {
  return (
    <button
      type="button"
      onClick={onClick}
      aria-pressed={selected}
      className={cn(
        "flex items-start justify-between gap-2 rounded-lg border p-3 text-left transition-colors",
        selected
          ? "border-primary bg-primary/5"
          : "border-border hover:border-muted-foreground/40"
      )}
    >
      <div className="space-y-1">
        <div className="flex items-center gap-2 text-sm font-medium">
          {icon}
          {title}
        </div>
        <p className="text-[11px] text-muted-foreground">{description}</p>
      </div>
      <span
        className={cn(
          "mt-0.5 flex size-4 shrink-0 items-center justify-center rounded-full border",
          selected ? "border-primary" : "border-muted-foreground/40"
        )}
      >
        {selected && <span className="size-2 rounded-full bg-primary" />}
      </span>
    </button>
  )
}

/**
 * Inline amber advisory used for both push (direct-commit) and pull (overwrite)
 * consequences.
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
 * Compact summary of a completed pull: counts, per-resource breakdown, and any
 * diagnostics.
 */
function PullResultSummary({ result }: { result: PullResult }) {
  const resourceCounts = resourceCountEntries(result)
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
                {RESOURCE_LABELS[resourceType] ?? resourceType}
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
        <div className="space-y-2">
          <h6 className="text-sm font-medium">Issues found:</h6>
          <div className="max-h-32 space-y-2 overflow-y-auto">
            {result.diagnostics.map((diagnostic) => (
              <div
                key={diagnostic.workflow_title || diagnostic.workflow_path}
                className="flex items-start gap-2 rounded bg-muted p-2 text-xs"
              >
                <AlertTriangleIcon className="mt-0.5 size-3 shrink-0 text-amber-500" />
                <div className="min-w-0 space-y-1">
                  <div className="font-medium">
                    {diagnostic.workflow_title || diagnostic.workflow_path}
                  </div>
                  <div className="text-muted-foreground">
                    {diagnostic.message}
                  </div>
                  <Badge variant="outline" className="text-xs">
                    {diagnostic.error_type}
                  </Badge>
                </div>
              </div>
            ))}
          </div>
        </div>
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
