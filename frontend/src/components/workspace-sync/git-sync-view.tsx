"use client"

import { ArrowDownIcon, ArrowUpIcon, GitBranchIcon } from "lucide-react"
import { type ReactNode, useState } from "react"
import type { VcsProvider, WorkspaceRead } from "@/client"
import {
  Empty,
  EmptyDescription,
  EmptyHeader,
  EmptyMedia,
  EmptyTitle,
} from "@/components/ui/empty"
import { SidebarTrigger } from "@/components/ui/sidebar"
import { Tabs, TabsContent, TabsList, TabsTrigger } from "@/components/ui/tabs"
import { getWorkspaceSyncBaseBranch } from "@/components/workspace-sync/branch-target-selector"
import { GitSyncConnectionPanel } from "@/components/workspace-sync/git-sync-connection"
import { GitSyncPullTab } from "@/components/workspace-sync/git-sync-pull"
import { GitSyncPushTab } from "@/components/workspace-sync/git-sync-push"
import { GitSyncRepoStatus } from "@/components/workspace-sync/git-sync-repo-status"
import {
  useRepositoryBranches,
  useRepositoryCommits,
} from "@/hooks/use-workspace-sync"
import { getRepoDisplayName } from "@/lib/git"
import { useQueryClient } from "@/lib/query"
import { cn } from "@/lib/utils"

type SyncDirection = "push" | "pull"

const SYNC_QUERY_KEYS = [
  "workflow-sync-branches",
  "repository_commits",
  "workspace-sync-export-preview",
  "workspace-sync-pull-preview",
]

interface GitSyncViewProps {
  workspace: WorkspaceRead
  canSync: boolean
  canManageConnection: boolean
}

/**
 * Full-page Git sync for a workspace: connect a repository, then push local
 * changes to, or pull changes from, its configured branch.
 */
export function GitSyncView({
  workspace,
  canSync,
  canManageConnection,
}: GitSyncViewProps) {
  const gitRepoUrl = workspace.settings?.git_repo_url || undefined
  if (!gitRepoUrl || !canSync) {
    return (
      <div className="flex h-full flex-col">
        <GitSyncHeader workspaceName={workspace.name} />
        {canManageConnection ? (
          <GitSyncConnectionPanel workspace={workspace} />
        ) : (
          <GitSyncConnectionEmptyState />
        )}
      </div>
    )
  }
  return (
    <GitSyncConnectedView
      workspace={workspace}
      gitRepoUrl={gitRepoUrl}
      provider={workspace.settings?.git_provider ?? "github"}
      canManageConnection={canManageConnection}
    />
  )
}

function GitSyncConnectedView({
  workspace,
  gitRepoUrl,
  provider,
  canManageConnection,
}: {
  workspace: WorkspaceRead
  gitRepoUrl: string
  provider: VcsProvider
  canManageConnection: boolean
}) {
  const workspaceId = workspace.id
  const queryClient = useQueryClient()
  const [direction, setDirection] = useState<SyncDirection>("push")
  const [showConnection, setShowConnection] = useState(false)

  const { branches, branchesIsLoading, branchesError } = useRepositoryBranches(
    workspaceId,
    {
      gitRepoUrl,
      provider,
      limit: 200,
      enabled: Boolean(gitRepoUrl),
    }
  )
  const baseBranch = getWorkspaceSyncBaseBranch(gitRepoUrl, branches)
  const { commits, commitsIsLoading, commitsError } = useRepositoryCommits(
    workspaceId,
    {
      branch: baseBranch,
      gitRepoUrl,
      provider,
      limit: 20,
      enabled: Boolean(gitRepoUrl && baseBranch),
    }
  )
  const repoDisplayName = getRepoDisplayName(gitRepoUrl)

  function invalidateSyncQueries() {
    return Promise.all(
      SYNC_QUERY_KEYS.map((key) =>
        queryClient.invalidateQueries({ queryKey: [key, workspaceId] })
      )
    )
  }

  return (
    <Tabs
      value={direction}
      onValueChange={(value) => setDirection(value as SyncDirection)}
      className="flex h-full flex-col"
    >
      <GitSyncHeader workspaceName={workspace.name}>
        {!showConnection && (
          <div className="ml-auto flex min-w-0 items-center gap-3">
            <GitSyncRepoStatus
              provider={provider}
              repoName={repoDisplayName ?? gitRepoUrl}
              branchesIsLoading={branchesIsLoading}
              hasBranchesError={Boolean(branchesError)}
              onEditConnection={
                canManageConnection ? () => setShowConnection(true) : undefined
              }
            />
            <span aria-hidden className="h-4 w-px shrink-0 bg-border" />
            <TabsList className="h-7 shrink-0 gap-0 overflow-hidden rounded-md border bg-transparent p-0">
              <TabsTrigger
                value="push"
                disableUnderline
                className={DIRECTION_TAB_CLASS_NAME}
              >
                <ArrowUpIcon className="size-3.5" />
                Push
              </TabsTrigger>
              <TabsTrigger
                value="pull"
                disableUnderline
                className={cn(DIRECTION_TAB_CLASS_NAME, "border-l")}
              >
                <ArrowDownIcon className="size-3.5" />
                Pull
              </TabsTrigger>
            </TabsList>
          </div>
        )}
      </GitSyncHeader>
      {showConnection && (
        <GitSyncConnectionPanel
          workspace={workspace}
          onBack={() => setShowConnection(false)}
          onSaved={() => void invalidateSyncQueries()}
        />
      )}
      {/* Stays mounted while the connection is open so tab state survives. */}
      <div
        className={cn(
          "flex min-h-0 flex-1 flex-col overflow-y-auto",
          showConnection && "hidden"
        )}
      >
        {/* Both tabs stay mounted so each keeps its form state. */}
        <TabsContent
          value="push"
          forceMount
          hidden={direction !== "push"}
          className="mt-0 flex-col data-[state=active]:flex data-[state=active]:flex-1"
        >
          <GitSyncPushTab
            workspaceId={workspaceId}
            persistedGitUrl={gitRepoUrl}
            provider={provider}
            repoBranches={branches}
            baseBranch={baseBranch}
            branchesIsLoading={branchesIsLoading}
            branchesError={branchesError}
          />
        </TabsContent>
        <TabsContent
          value="pull"
          forceMount
          hidden={direction !== "pull"}
          className="mt-0 min-w-0 flex-col data-[state=active]:flex data-[state=active]:flex-1"
        >
          <GitSyncPullTab
            workspaceId={workspaceId}
            workspaceName={workspace.name}
            provider={provider}
            baseBranch={baseBranch}
            commits={commits}
            commitsIsLoading={commitsIsLoading}
            commitsError={commitsError}
          />
        </TabsContent>
      </div>
    </Tabs>
  )
}

/** Matches the header view toggles on the workflows and cases pages. */
const DIRECTION_TAB_CLASS_NAME =
  "h-full gap-1.5 rounded-none px-2.5 text-xs data-[state=active]:bg-background data-[state=active]:text-foreground data-[state=inactive]:bg-accent data-[state=inactive]:text-muted-foreground data-[state=inactive]:hover:bg-muted/50"

/**
 * Page header in the controls-header shape. ControlsHeader has no config for
 * this route, so the page owns its header and the direction state.
 */
export function GitSyncHeader({
  workspaceName,
  children,
}: {
  workspaceName?: string
  children?: ReactNode
}) {
  return (
    <header className="flex h-10 shrink-0 items-center gap-3 overflow-hidden border-b px-3">
      <SidebarTrigger className="h-7 w-7 flex-shrink-0" />
      <div className="flex shrink-0 items-center gap-2 text-sm">
        {workspaceName && (
          <>
            <span className="max-w-40 truncate text-muted-foreground">
              {workspaceName}
            </span>
            <span className="text-muted-foreground/60">/</span>
          </>
        )}
        <h1 className="font-medium">Git Sync</h1>
      </div>
      {children}
    </header>
  )
}

function GitSyncConnectionEmptyState() {
  return (
    <Empty className="flex-1">
      <EmptyHeader>
        <EmptyMedia variant="icon">
          <GitBranchIcon className="size-6" />
        </EmptyMedia>
        <EmptyTitle>No repository connected</EmptyTitle>
        <EmptyDescription>
          Ask a workspace admin to connect one. Once it&apos;s connected, you
          can push and pull here.
        </EmptyDescription>
      </EmptyHeader>
    </Empty>
  )
}
