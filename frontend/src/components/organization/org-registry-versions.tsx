"use client"

import {
  AlertTriangleIcon,
  GitBranchIcon,
  GitCommitIcon,
  LockIcon,
  RefreshCcw,
} from "lucide-react"
import Link from "next/link"
import { type ReactNode, useMemo, useState } from "react"
import type {
  GitCommitInfo,
  RegistryRepositoriesSyncRegistryRepositoryData,
  tracecat__registry__repositories__schemas__RegistryVersionRead,
} from "@/client"
import { useScopeCheck } from "@/components/auth/scope-guard"
import { CenteredSpinner } from "@/components/loading/spinner"
import {
  OrgRegistryVersionsTable,
  type VersionRow,
} from "@/components/organization/org-registry-versions-table"
import { SyncRepositoryDialog } from "@/components/registry/dialogs/repository-sync-dialog"
import { VersionDiffDialog } from "@/components/registry/dialogs/version-diff-dialog"
import {
  getCustomRegistryRepository,
  shortVersion,
} from "@/components/registry/utils"
import { Alert, AlertDescription, AlertTitle } from "@/components/ui/alert"
import {
  AlertDialog,
  AlertDialogAction,
  AlertDialogCancel,
  AlertDialogContent,
  AlertDialogDescription,
  AlertDialogFooter,
  AlertDialogHeader,
  AlertDialogTitle,
} from "@/components/ui/alert-dialog"
import { Button } from "@/components/ui/button"
import {
  Empty,
  EmptyContent,
  EmptyDescription,
  EmptyHeader,
  EmptyMedia,
  EmptyTitle,
} from "@/components/ui/empty"
import { useRegistryVersions } from "@/hooks/use-registry-versions"
import { getApiErrorDetail } from "@/lib/errors"
import { getRelativeTime } from "@/lib/event-history"
import { getRepoRef } from "@/lib/git"
import {
  useOrgGitSettings,
  useRegistryRepositories,
  useRepositoryCommits,
} from "@/lib/hooks"

type RegistryVersionRead =
  tracecat__registry__repositories__schemas__RegistryVersionRead

const DEFAULT_BRANCH = "main"

/** Props for {@link OrgRegistryVersionsShell}. */
export interface OrgRegistryVersionsShellProps {
  /** Rendered on the right of the page header. */
  action?: ReactNode
  children: ReactNode
}

/** Standard settings layout and header for the Versions page. */
export function OrgRegistryVersionsShell({
  action,
  children,
}: OrgRegistryVersionsShellProps) {
  return (
    <div className="size-full overflow-auto">
      <div className="container flex h-full max-w-[1000px] flex-col space-y-12">
        <div className="flex w-full items-start justify-between gap-4">
          <div className="items-start space-y-3 text-left">
            <h2 className="text-2xl font-semibold tracking-tight">Versions</h2>
            <p className="text-base text-muted-foreground">
              Sync commits from the remote repository and choose which version
              is active.
            </p>
          </div>
          {action}
        </div>
        {children}
      </div>
    </div>
  )
}

/** Normalise an API error detail into a sentence with terminal punctuation. */
function formatErrorSentence(detail: string | null | undefined): string {
  const text = detail?.trim() || "Unknown error"
  return /[.!?]$/.test(text) ? text : `${text}.`
}

function commitRowKey(sha: string): string {
  return `commit:${sha}`
}

function versionRowKey(versionId: string): string {
  return `version:${versionId}`
}

function buildRows(
  commits: GitCommitInfo[],
  versions: RegistryVersionRead[],
  currentVersionId: string | null
): { commitRows: VersionRow[]; otherRows: VersionRow[] } {
  const versionsBySha = new Map<string, RegistryVersionRead[]>()
  for (const version of versions) {
    if (!version.commit_sha) {
      continue
    }
    const bucket = versionsBySha.get(version.commit_sha) ?? []
    bucket.push(version)
    versionsBySha.set(version.commit_sha, bucket)
  }

  const commitShas = new Set(commits.map((commit) => commit.sha))
  const commitRows: VersionRow[] = []
  commits.forEach((commit, index) => {
    const isHead = index === 0
    const synced = versionsBySha.get(commit.sha) ?? []
    if (synced.length === 0) {
      commitRows.push({
        key: commitRowKey(commit.sha),
        commit,
        version: null,
        isHead,
        isCurrent: false,
        showCommitDetails: true,
      })
      return
    }
    synced.forEach((version, versionIndex) => {
      commitRows.push({
        key: versionRowKey(version.id),
        commit,
        version,
        isHead,
        isCurrent: version.id === currentVersionId,
        showCommitDetails: versionIndex === 0,
      })
    })
  })

  const otherRows: VersionRow[] = versions
    .filter(
      (version) => !version.commit_sha || !commitShas.has(version.commit_sha)
    )
    .map((version) => ({
      key: versionRowKey(version.id),
      commit: null,
      version,
      isHead: false,
      isCurrent: version.id === currentVersionId,
      showCommitDetails: false,
    }))

  return { commitRows, otherRows }
}

type SyncTarget = {
  commit: GitCommitInfo | null
}

type CompareTarget = {
  baseId: string
  compareToId: string | null
}

/** Versions page body: commit list, sync, promote, compare, and delete. */
export function OrgRegistryVersions() {
  const canRead = useScopeCheck("org:registry:read")
  const canUpdate = useScopeCheck("org:registry:update") === true
  const canDelete = useScopeCheck("org:registry:delete") === true

  const { gitSettings, gitSettingsIsLoading } = useOrgGitSettings()
  const { repos, reposIsLoading, syncRepo, syncRepoIsPending } =
    useRegistryRepositories()
  const repo = getCustomRegistryRepository(repos)
  const isConnected = Boolean(gitSettings?.git_repo_url) && repo !== null
  const repositoryId = canRead === true && isConnected && repo ? repo.id : null
  // The origin's `@ref` suffix selects the branch; the backend defaults to main.
  const branch = repo
    ? (getRepoRef(repo.origin) ?? DEFAULT_BRANCH)
    : DEFAULT_BRANCH

  const { commits, commitsIsLoading, commitsError } = useRepositoryCommits(
    repositoryId,
    { branch }
  )
  const {
    versions,
    versionsIsLoading,
    promoteVersion,
    promoteVersionIsPending,
    deleteVersion,
    deleteVersionIsPending,
  } = useRegistryVersions(repositoryId)

  const [syncTarget, setSyncTarget] = useState<SyncTarget | null>(null)
  const [compareTarget, setCompareTarget] = useState<CompareTarget | null>(null)
  const [versionPendingDelete, setVersionPendingDelete] =
    useState<RegistryVersionRead | null>(null)
  const [pendingKey, setPendingKey] = useState<string | null>(null)

  const currentVersionId = repo?.current_version_id ?? null
  const { commitRows, otherRows } = useMemo(
    () => buildRows(commits ?? [], versions ?? [], currentVersionId),
    [commits, versions, currentVersionId]
  )

  const mutationInFlight =
    syncRepoIsPending || promoteVersionIsPending || deleteVersionIsPending

  async function handleSyncRepo(
    params: RegistryRepositoriesSyncRegistryRepositoryData
  ) {
    const targetSha = params.requestBody?.target_commit_sha
    if (targetSha) {
      setPendingKey(commitRowKey(targetSha))
    } else {
      setPendingKey(commitRows[0]?.key ?? null)
    }
    try {
      return await syncRepo(params)
    } finally {
      setPendingKey(null)
    }
  }

  async function handlePromote(version: RegistryVersionRead) {
    setPendingKey(versionRowKey(version.id))
    try {
      await promoteVersion({
        versionId: version.id,
        versionName: shortVersion(version.version),
      })
    } catch (error) {
      console.error("Failed to promote version", error)
    } finally {
      setPendingKey(null)
    }
  }

  function handleCompare(version: RegistryVersionRead) {
    let compareToId: string | null = null
    if (currentVersionId && currentVersionId !== version.id) {
      compareToId = currentVersionId
    } else {
      compareToId = versions?.find((v) => v.id !== version.id)?.id ?? null
    }
    setCompareTarget({ baseId: version.id, compareToId })
  }

  async function handleConfirmDelete() {
    if (!versionPendingDelete) {
      return
    }
    const versionId = versionPendingDelete.id
    setVersionPendingDelete(null)
    setPendingKey(versionRowKey(versionId))
    try {
      await deleteVersion(versionId)
    } catch (error) {
      console.error("Failed to delete version", error)
    } finally {
      setPendingKey(null)
    }
  }

  if (canRead === undefined || gitSettingsIsLoading || reposIsLoading) {
    return (
      <OrgRegistryVersionsShell>
        <CenteredSpinner />
      </OrgRegistryVersionsShell>
    )
  }

  if (canRead !== true) {
    return (
      <OrgRegistryVersionsShell>
        <Empty className="border-0">
          <EmptyHeader>
            <EmptyMedia variant="icon">
              <LockIcon />
            </EmptyMedia>
            <EmptyTitle>You don't have access to registry versions</EmptyTitle>
            <EmptyDescription>
              Ask an organization admin for the registry read permission.
            </EmptyDescription>
          </EmptyHeader>
        </Empty>
      </OrgRegistryVersionsShell>
    )
  }

  if (!isConnected || !repo) {
    return (
      <OrgRegistryVersionsShell>
        <Empty className="border-0">
          <EmptyHeader>
            <EmptyMedia variant="icon">
              <GitBranchIcon />
            </EmptyMedia>
            <EmptyTitle>No repository connected</EmptyTitle>
            <EmptyDescription>
              Connect a Git repository before syncing registry versions.
            </EmptyDescription>
          </EmptyHeader>
          <EmptyContent>
            <Button variant="outline" size="sm" asChild>
              <Link href="/organization/settings/custom-registry">
                Connect a repository
              </Link>
            </Button>
          </EmptyContent>
        </Empty>
      </OrgRegistryVersionsShell>
    )
  }

  const currentVersion = versions?.find((v) => v.id === currentVersionId)
  // `repo.commit_sha` is the last *synced* SHA; after a promote it can differ
  // from the current version, so prefer the current version's SHA.
  const currentCommitSha = currentVersion?.commit_sha ?? repo.commit_sha
  const lastSyncedLabel = repo.last_synced_at
    ? getRelativeTime(new Date(repo.last_synced_at))
    : "never"

  const isTableLoading = commitsIsLoading || versionsIsLoading
  const hasRows = commitRows.length > 0 || otherRows.length > 0
  const showNoCommits =
    !isTableLoading &&
    !commitsError &&
    !hasRows &&
    (versions?.length ?? 0) === 0

  const syncButton = canUpdate ? (
    <Button
      variant="outline"
      size="sm"
      onClick={() => setSyncTarget({ commit: null })}
      disabled={mutationInFlight}
    >
      <RefreshCcw
        className={
          syncRepoIsPending ? "mr-1.5 size-3.5 animate-spin" : "mr-1.5 size-3.5"
        }
      />
      Sync from remote
    </Button>
  ) : null

  return (
    <OrgRegistryVersionsShell action={syncButton}>
      <div className="space-y-4">
        <div className="flex flex-wrap items-center gap-x-2 gap-y-1 text-sm text-muted-foreground">
          <span className="break-all font-mono text-xs text-foreground">
            {repo.origin}
          </span>
          <span aria-hidden="true">·</span>
          <span>Last synced {lastSyncedLabel}</span>
        </div>

        {commitsError && (
          <Alert>
            <AlertTriangleIcon className="size-4" />
            <AlertTitle>Couldn't load commits from {branch}</AlertTitle>
            <AlertDescription>
              {formatErrorSentence(getApiErrorDetail(commitsError))} Check the{" "}
              <Link
                href="/organization/settings/custom-registry"
                className="underline underline-offset-2"
              >
                SSH key on the Repository page
              </Link>{" "}
              and that the branch exists.
            </AlertDescription>
          </Alert>
        )}

        {isTableLoading && <CenteredSpinner />}

        {showNoCommits && (
          <Empty className="border-0">
            <EmptyHeader>
              <EmptyMedia variant="icon">
                <GitCommitIcon />
              </EmptyMedia>
              <EmptyTitle>No commits found</EmptyTitle>
              <EmptyDescription>
                The {branch} branch of the remote repository has no commits yet.
              </EmptyDescription>
            </EmptyHeader>
          </Empty>
        )}

        {!isTableLoading && hasRows && (
          <OrgRegistryVersionsTable
            commitRows={commitRows}
            otherRows={otherRows}
            canUpdate={canUpdate}
            canDelete={canDelete}
            canCompare={(versions?.length ?? 0) > 1}
            pendingKey={pendingKey}
            mutationInFlight={mutationInFlight}
            onSyncCommit={(commit) => setSyncTarget({ commit })}
            onPromote={handlePromote}
            onCompare={handleCompare}
            onDelete={setVersionPendingDelete}
          />
        )}
      </div>

      <SyncRepositoryDialog
        open={syncTarget !== null}
        onOpenChange={(open) => {
          if (!open) {
            setSyncTarget(null)
          }
        }}
        selectedRepo={repo}
        syncRepo={handleSyncRepo}
        syncRepoIsPending={syncRepoIsPending}
        targetCommit={syncTarget?.commit ?? null}
        currentCommitSha={currentCommitSha}
      />

      <VersionDiffDialog
        open={compareTarget !== null}
        onOpenChange={(open) => {
          if (!open) {
            setCompareTarget(null)
          }
        }}
        repositoryId={repo.id}
        versions={versions ?? []}
        initialBaseId={compareTarget?.baseId ?? null}
        initialCompareId={compareTarget?.compareToId ?? null}
      />

      <AlertDialog
        open={versionPendingDelete !== null}
        onOpenChange={(open) => {
          if (!open) {
            setVersionPendingDelete(null)
          }
        }}
      >
        <AlertDialogContent>
          <AlertDialogHeader>
            <AlertDialogTitle>Delete registry version?</AlertDialogTitle>
            <AlertDialogDescription>
              This permanently removes version{" "}
              <span className="font-mono">
                {versionPendingDelete
                  ? shortVersion(versionPendingDelete.version)
                  : ""}
              </span>
              . Only versions that are not current and not used by published
              workflows can be deleted.
            </AlertDialogDescription>
          </AlertDialogHeader>
          <AlertDialogFooter>
            <AlertDialogCancel>Cancel</AlertDialogCancel>
            <AlertDialogAction
              onClick={handleConfirmDelete}
              className="bg-destructive text-destructive-foreground hover:bg-destructive/90"
            >
              Delete version
            </AlertDialogAction>
          </AlertDialogFooter>
        </AlertDialogContent>
      </AlertDialog>
    </OrgRegistryVersionsShell>
  )
}
