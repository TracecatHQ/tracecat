"use client"

import {
  AlertTriangleIcon,
  ArrowDownIcon,
  ArrowUpIcon,
  GitBranchIcon,
  PencilIcon,
} from "lucide-react"
import { useState } from "react"
import type { VcsProvider, WorkspaceRead } from "@/client"
import { Button } from "@/components/ui/button"
import { Skeleton } from "@/components/ui/skeleton"
import { Tabs, TabsContent, TabsList, TabsTrigger } from "@/components/ui/tabs"
import { getWorkspaceSyncBaseBranch } from "@/components/workspace-sync/branch-target-selector"
import { WorkspaceSyncConnectionForm } from "@/components/workspace-sync/connection-form"
import { WorkspaceSyncPullTab } from "@/components/workspace-sync/pull-tab"
import { WorkspaceSyncPushTab } from "@/components/workspace-sync/push-tab"
import {
  useRepositoryBranches,
  useRepositoryCommits,
} from "@/hooks/use-workspace-sync"
import { getRelativeTime } from "@/lib/event-history"
import { getRepoDisplayName } from "@/lib/git"
import { useGitHubAppRepositories } from "@/lib/hooks"

type SyncMode = "push" | "pull"

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
  const persistedProvider: VcsProvider =
    workspace.settings?.git_provider ?? "github"
  const {
    repositories = [],
    repositoriesIsLoading,
    repositoriesError,
  } = useGitHubAppRepositories(workspace.id, {
    enabled: persistedProvider === "github",
  })

  const [isEditingConnection, setIsEditingConnection] = useState(false)
  const [mode, setMode] = useState<SyncMode>("push")

  const {
    branches: repoBranches,
    branchesIsLoading,
    branchesError,
  } = useRepositoryBranches(workspace.id, {
    enabled: Boolean(persistedGitUrl),
    gitRepoUrl: persistedGitUrl,
    provider: persistedProvider,
    limit: 200,
  })
  const baseBranch = getWorkspaceSyncBaseBranch(persistedGitUrl, repoBranches)

  const { commits, commitsIsLoading, commitsError } = useRepositoryCommits(
    workspace.id,
    {
      branch: baseBranch,
      gitRepoUrl: persistedGitUrl,
      provider: persistedProvider,
      limit: 20,
      enabled: Boolean(persistedGitUrl) && Boolean(baseBranch),
    }
  )
  const repoDisplayName = getRepoDisplayName(persistedGitUrl)
  const latestCommit = commits?.[0]
  const showConnectionForm = !persistedGitUrl || isEditingConnection

  return (
    <div className="space-y-6">
      {showConnectionForm ? (
        <WorkspaceSyncConnectionForm
          workspaceId={workspace.id}
          persistedGitUrl={persistedGitUrl}
          persistedProvider={persistedProvider}
          repositories={repositories}
          repositoriesIsLoading={repositoriesIsLoading}
          repositoriesError={repositoriesError}
          onClose={() => setIsEditingConnection(false)}
        />
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
                defaultBranch={baseBranch}
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
            onClick={() => setIsEditingConnection(true)}
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
            <WorkspaceSyncPushTab
              workspaceId={workspace.id}
              persistedGitUrl={persistedGitUrl}
              provider={persistedProvider}
              repoDisplayName={repoDisplayName}
              repoBranches={repoBranches}
              baseBranch={baseBranch}
              branchesIsLoading={branchesIsLoading}
              branchesError={branchesError}
            />
          </TabsContent>

          <TabsContent value="pull" className="space-y-4">
            <WorkspaceSyncPullTab
              workspaceId={workspace.id}
              provider={persistedProvider}
              commits={commits}
              commitsIsLoading={commitsIsLoading}
              commitsError={commitsError}
            />
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
