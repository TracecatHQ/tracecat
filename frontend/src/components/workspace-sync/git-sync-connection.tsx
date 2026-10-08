"use client"

import {
  AlertCircleIcon,
  AlertTriangleIcon,
  ArrowLeftIcon,
  ArrowUpRight,
  BookMarkedIcon,
  ChevronDownIcon,
  GitBranchIcon,
  GitCommitHorizontalIcon,
  GlobeIcon,
  LockIcon,
  PencilIcon,
} from "lucide-react"
import Link from "next/link"
import { type FormEvent, type ReactNode, useState } from "react"
import type {
  GitBranchInfo,
  GitHubAppRepository,
  VcsProvider,
  WorkspaceRead,
} from "@/client"
import { useScopeCheck } from "@/components/auth/scope-guard"
import {
  VcsProviderIcon,
  VcsProviderLogo,
} from "@/components/organization/vcs-icons"
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
  DropdownMenu,
  DropdownMenuContent,
  DropdownMenuItem,
  DropdownMenuTrigger,
} from "@/components/ui/dropdown-menu"
import {
  Select,
  SelectContent,
  SelectItem,
  SelectTrigger,
  SelectValue,
} from "@/components/ui/select"
import { Skeleton } from "@/components/ui/skeleton"
import {
  getWorkspaceSyncBaseBranch,
  getWorkspaceSyncConfiguredRef,
  getWorkspaceSyncDefaultBranch,
  withWorkspaceSyncConfiguredRef,
} from "@/components/workspace-sync/branch-target-selector"
import { getReviewRequestLabel } from "@/components/workspace-sync/push-target-policy"
import {
  type ConfiguredGitProvider,
  type GitSyncProvidersState,
  useGitSyncProviders,
} from "@/hooks/use-git-sync-providers"
import {
  useRepositoryBranches,
  useRepositoryCommits,
} from "@/hooks/use-workspace-sync"
import { getRelativeTime } from "@/lib/event-history"
import { getRepoDisplayName } from "@/lib/git"
import {
  DEFAULT_GIT_HOSTS,
  findAppRepository,
  GIT_PROVIDER_LABELS,
  type GitRemoteParseResult,
  type GitRemoteTarget,
  getAppRepositoryGitUrl,
  getGitSshHost,
  parseGitRemote,
} from "@/lib/git-remote"
import { useGitHubAppRepositories, useWorkspaceSettings } from "@/lib/hooks"
import { useQueryClient } from "@/lib/query"
import { cn } from "@/lib/utils"

const ALL_PROVIDERS: VcsProvider[] = [
  "github",
  "gitlab",
  "bitbucket",
  "bitbucket_data_center",
]

const SUBTITLE =
  "Push this workspace's workflows to Git and pull reviewed changes back."

const SYNC_QUERY_KEYS = [
  "workflow-sync-branches",
  "repository_commits",
  "workspace-sync-export-preview",
  "workspace-sync-pull-preview",
]

interface GitSyncConnectionPanelProps {
  workspace: WorkspaceRead
  /** Shows connect, change and disconnect actions. Defaults to true. */
  canManageConnection?: boolean
  onBack?: () => void
  onSaved?: () => void
}

/**
 * Inline page body for the workspace repository connection: the remote line
 * when nothing is connected, otherwise the connected repository with change
 * and disconnect actions.
 */
export function GitSyncConnectionPanel({
  workspace,
  canManageConnection = true,
  onBack,
  onSaved,
}: GitSyncConnectionPanelProps) {
  const isConnected = Boolean(workspace.settings?.git_repo_url)
  const [isEditing, setIsEditing] = useState(false)
  const showRemoteLine = canManageConnection && (!isConnected || isEditing)
  const providers = useGitSyncProviders({ enabled: showRemoteLine })
  const canManageOrgSettings = useScopeCheck("org:settings:update") === true

  let body: ReactNode = null
  if (!showRemoteLine) {
    // Connected and not editing: the repository summary is the whole body.
  } else if (providers.kind === "loading") {
    body = <ConnectSkeleton />
  } else if (providers.kind === "known" && providers.providers.length === 0) {
    body = <NoProviderState canSetUp={canManageOrgSettings} />
  } else {
    body = (
      <GitSyncConnect
        workspace={workspace}
        providers={providers}
        canManageOrgSettings={canManageOrgSettings}
        onSaved={() => {
          onSaved?.()
          if (isConnected) {
            setIsEditing(false)
            onBack?.()
          }
        }}
      />
    )
  }

  return (
    <div className="min-h-0 flex-1 overflow-auto">
      <div className="mx-auto w-full max-w-[620px] space-y-8 px-6 pb-16 pt-16 md:pt-24">
        {onBack && !isEditing && (
          <Button
            type="button"
            variant="ghost"
            size="sm"
            className="-ml-2 gap-1.5 text-muted-foreground hover:text-foreground"
            onClick={onBack}
          >
            <ArrowLeftIcon className="size-3.5" />
            {isConnected ? "Back to changes" : "Back"}
          </Button>
        )}
        <div className="space-y-2">
          <h2 className="text-2xl font-semibold tracking-tight">
            {isConnected ? "Repository connection" : "Connect a repository"}
          </h2>
          <p className="text-sm text-muted-foreground">
            {isConnected
              ? `Pushes start from the branch below and open ${getReviewRequestLabel(workspace.settings?.git_provider ?? "github")}s into it. Pulls read its commits.`
              : SUBTITLE}
          </p>
        </div>
        {isConnected && (
          <ConnectedRepository
            workspace={workspace}
            canManageConnection={canManageConnection}
            isEditing={isEditing}
            onEditingChange={setIsEditing}
          />
        )}
        {body}
      </div>
    </div>
  )
}

function ConnectSkeleton() {
  return (
    <div className="space-y-3" aria-hidden="true">
      <Skeleton className="h-10 w-full rounded-lg" />
      <Skeleton className="h-3 w-2/3" />
    </div>
  )
}

function NoProviderState({ canSetUp }: { canSetUp: boolean }) {
  return (
    <div className="space-y-3">
      <h3 className="text-base font-semibold">
        Your organization hasn&apos;t set up a Git provider
      </h3>
      {canSetUp ? (
        <>
          <p className="text-sm text-muted-foreground">
            Set up GitHub, GitLab or Bitbucket once in Organization › Git
            providers. Every workspace can then connect a repository.
          </p>
          <Button variant="outline" size="sm" className="gap-1.5" asChild>
            <Link href="/organization/vcs">
              Set up a Git provider
              <ArrowUpRight className="size-3.5" />
            </Link>
          </Button>
        </>
      ) : (
        <p className="text-sm text-muted-foreground">
          Ask an org admin to set one up in Organization › Git providers. You
          can connect a repository here once they have.
        </p>
      )}
    </div>
  )
}

interface GitSyncConnectProps {
  workspace: WorkspaceRead
  providers: Exclude<GitSyncProvidersState, { kind: "loading" }>
  canManageOrgSettings: boolean
  onSaved: () => void
}

/** Provider choice, remote line, and repository list for one workspace. */
function GitSyncConnect({
  workspace,
  providers,
  canManageOrgSettings,
  onSaved,
}: GitSyncConnectProps) {
  const persistedUrl = workspace.settings?.git_repo_url || undefined
  const persistedProvider = workspace.settings?.git_provider ?? undefined
  const orgConfigured = providers.kind === "known"
  const candidates = getCandidateProviders(
    providers,
    persistedProvider,
    persistedUrl
  )
  const [provider, setProvider] = useState<VcsProvider | undefined>(() =>
    getInitialProvider(providers, persistedProvider, persistedUrl)
  )
  const [input, setInput] = useState(() =>
    getPrefill(persistedUrl, persistedProvider, candidates)
  )
  const [hasAttempted, setHasAttempted] = useState(false)
  const { updateWorkspace, isUpdating } = useWorkspaceSettings(workspace.id)

  const isGitHub = provider === "github"
  const { repositories, repositoriesIsLoading, repositoriesError } =
    useGitHubAppRepositories(workspace.id, { enabled: isGitHub })
  const appRepositories = repositories ?? []
  const repositoriesLoaded = Boolean(repositories) && !repositoriesError

  if (!provider) {
    return (
      <ProviderCards
        providers={candidates}
        onSelect={(id) => setProvider(id)}
      />
    )
  }

  const candidate = candidates.find((entry) => entry.id === provider) ?? {
    id: provider,
  }
  const target = getRemoteTarget(
    candidate,
    orgConfigured,
    repositoriesLoaded ? appRepositories : []
  )
  const parsed = parseGitRemote(input, target)
  const resolution = resolveConnection(parsed, {
    provider,
    repositories: appRepositories,
    repositoriesLoaded,
    canManageOrgSettings,
  })
  const error =
    hasAttempted && resolution.kind === "invalid"
      ? resolution.message
      : undefined
  const canPickProvider = candidates.length > 1

  async function handleSubmit(event: FormEvent<HTMLFormElement>) {
    event.preventDefault()
    setHasAttempted(true)
    if (resolution.kind !== "ok" || !provider) {
      return
    }
    try {
      await updateWorkspace({
        settings: { git_provider: provider, git_repo_url: resolution.gitUrl },
      })
    } catch {
      // useWorkspaceSettings reports the failure with a toast.
      return
    }
    onSaved()
  }

  function handleProviderChange(id: VcsProvider) {
    setProvider(id)
    setHasAttempted(false)
  }

  return (
    <div className="space-y-8">
      <form onSubmit={handleSubmit} className="space-y-2.5">
        <div className="flex items-center gap-2">
          <div
            className={cn(
              "flex h-10 min-w-0 flex-1 items-center overflow-hidden rounded-lg border bg-background",
              "focus-within:ring-1 focus-within:ring-inset focus-within:ring-ring",
              error && "border-destructive"
            )}
          >
            <HostSegment
              provider={provider}
              host={target.host}
              candidates={canPickProvider ? candidates : undefined}
              onChange={handleProviderChange}
            />
            <input
              aria-label="Repository"
              aria-invalid={Boolean(error)}
              className="h-full min-w-0 flex-1 bg-transparent px-3 font-mono text-sm outline-none placeholder:text-muted-foreground/70"
              placeholder={getPlaceholder(provider, target.host)}
              value={input}
              onChange={(event) => setInput(event.target.value)}
              autoComplete="off"
              spellCheck={false}
            />
          </div>
          <Button
            type="submit"
            className="h-10 shrink-0 px-4"
            disabled={isUpdating || parsed.kind === "empty"}
          >
            {getSubmitLabel(Boolean(persistedUrl), isUpdating)}
          </Button>
        </div>
        <div className="space-y-1.5 text-xs">
          {error && (
            <p
              role="alert"
              className="flex items-start gap-1.5 text-destructive"
            >
              <AlertCircleIcon className="mt-px size-3.5 shrink-0" />
              <span>{error}</span>
            </p>
          )}
          {parsed.kind === "ok" && (
            <p className="text-muted-foreground">
              Saved as{" "}
              <code className="break-all font-mono text-foreground">
                {resolution.kind === "ok"
                  ? resolution.gitUrl
                  : parsed.remote.gitUrl}
              </code>
            </p>
          )}
          <p className="text-muted-foreground">
            {getAccessLine(provider, orgConfigured, appRepositories)}
          </p>
        </div>
      </form>
      <div className="border-t pt-6">
        {isGitHub ? (
          <AppRepositoryList
            repositories={appRepositories}
            isLoading={repositoriesIsLoading}
            hasError={Boolean(repositoriesError) && !repositories?.length}
            filter={parsed.kind === "ok" ? parsed.remote.path : input.trim()}
            onSelect={(repository) => {
              setInput(repository.full_name)
              setHasAttempted(false)
            }}
          />
        ) : (
          <p className="text-sm text-muted-foreground">
            {getProviderHint(provider, target.host)}
          </p>
        )}
      </div>
    </div>
  )
}

/** Candidate list for the picker: configured providers, or all of them. */
function getCandidateProviders(
  providers: Exclude<GitSyncProvidersState, { kind: "loading" }>,
  persistedProvider: VcsProvider | undefined,
  persistedUrl: string | undefined
): ConfiguredGitProvider[] {
  if (providers.kind === "known") {
    return providers.providers
  }
  const persistedHost = getGitSshHost(persistedUrl)
  return ALL_PROVIDERS.map((id) => ({
    id,
    host:
      DEFAULT_GIT_HOSTS[id] ??
      (id === persistedProvider ? persistedHost : undefined),
  }))
}

function getInitialProvider(
  providers: Exclude<GitSyncProvidersState, { kind: "loading" }>,
  persistedProvider: VcsProvider | undefined,
  persistedUrl: string | undefined
): VcsProvider | undefined {
  if (providers.kind === "unknown") {
    return persistedProvider ?? "github"
  }
  const configured = providers.providers
  if (configured.length === 1) {
    return configured[0].id
  }
  // Several providers: ask first, unless editing an existing connection.
  if (
    persistedUrl &&
    configured.some((entry) => entry.id === persistedProvider)
  ) {
    return persistedProvider
  }
  return undefined
}

/** Prefill a short path when the stored URL is the plain form for its host. */
function getPrefill(
  persistedUrl: string | undefined,
  persistedProvider: VcsProvider | undefined,
  candidates: ConfiguredGitProvider[]
) {
  if (!persistedUrl) {
    return ""
  }
  const host = getGitSshHost(persistedUrl)
  const path = getRepoDisplayName(persistedUrl)
  const providerHost =
    candidates.find((entry) => entry.id === persistedProvider)?.host ??
    (persistedProvider ? DEFAULT_GIT_HOSTS[persistedProvider] : undefined)
  if (
    host &&
    path &&
    host === providerHost &&
    persistedUrl === `git+ssh://git@${host}/${path}.git`
  ) {
    return path
  }
  return persistedUrl
}

function getRemoteTarget(
  candidate: ConfiguredGitProvider,
  orgConfigured: boolean,
  repositories: GitHubAppRepository[]
): GitRemoteTarget {
  if (candidate.id === "github") {
    // The GitHub status has no host; app repository URLs do.
    const appHost = getGitSshHost(repositories[0]?.git_url)
    return {
      provider: "github",
      host: appHost ?? "github.com",
      hostIsKnown: Boolean(appHost),
      orgConfigured,
    }
  }
  if (candidate.id === "bitbucket") {
    return {
      provider: "bitbucket",
      host: "bitbucket.org",
      hostIsKnown: true,
      orgConfigured,
    }
  }
  return {
    provider: candidate.id,
    host: candidate.host,
    hostIsKnown: orgConfigured && Boolean(candidate.host),
    orgConfigured,
  }
}

type ConnectionResolution =
  | { kind: "empty" }
  | { kind: "invalid"; message: string }
  | { kind: "ok"; gitUrl: string }

/**
 * Final URL to save. GitHub input must be a repository the app can reach
 * when the app's repository list has loaded.
 */
function resolveConnection(
  parsed: GitRemoteParseResult,
  {
    provider,
    repositories,
    repositoriesLoaded,
    canManageOrgSettings,
  }: {
    provider: VcsProvider
    repositories: GitHubAppRepository[]
    repositoriesLoaded: boolean
    canManageOrgSettings: boolean
  }
): ConnectionResolution {
  if (parsed.kind !== "ok") {
    return parsed
  }
  const { remote } = parsed
  if (provider !== "github" || !repositoriesLoaded) {
    return { kind: "ok", gitUrl: remote.gitUrl }
  }
  const repository = findAppRepository(remote, repositories)
  if (!repository) {
    const nextStep = canManageOrgSettings
      ? "Add it to the app's repositories on GitHub, then try again."
      : "Ask an org admin to add it to the app's repositories."
    return {
      kind: "invalid",
      message: `The GitHub App can't reach ${remote.path}. ${nextStep}`,
    }
  }
  if (remote.ref) {
    return { kind: "ok", gitUrl: remote.gitUrl }
  }
  return { kind: "ok", gitUrl: getAppRepositoryGitUrl(repository) }
}

function getSubmitLabel(isConnected: boolean, isUpdating: boolean) {
  if (isUpdating) {
    return isConnected ? "Saving..." : "Connecting..."
  }
  return isConnected ? "Save" : "Connect"
}

function getPlaceholder(provider: VcsProvider, host: string | undefined) {
  switch (provider) {
    case "github":
      return "TracecatHQ/detections or a repository URL"
    case "gitlab":
      return `https://${host ?? "gitlab.example.com"}/group/project`
    case "bitbucket":
      return "workspace/repository or a repository URL"
    case "bitbucket_data_center":
      return `https://${host ?? "bitbucket.example.com"}/projects/PROJECT/repos/repository`
  }
}

function getProviderHint(provider: VcsProvider, host: string | undefined) {
  const source = host ?? GIT_PROVIDER_LABELS[provider]
  if (provider === "gitlab") {
    return `Paste the repository URL from ${source}. Nested groups work.`
  }
  return `Paste the repository URL from ${source}.`
}

/** Access source for the provider, claiming only what the data supports. */
function getAccessLine(
  provider: VcsProvider,
  orgConfigured: boolean,
  repositories: GitHubAppRepository[]
) {
  let source: string
  switch (provider) {
    case "github": {
      const accounts = new Set(
        repositories.map((repository) => repository.installation_account)
      )
      const [account] = accounts
      if (accounts.size === 1 && account) {
        return `Access through the GitHub App on ${account}, set up by your organization.`
      }
      source = "GitHub App"
      break
    }
    case "gitlab":
      source = "GitLab access token"
      break
    case "bitbucket":
      source = "Bitbucket Cloud API token"
      break
    case "bitbucket_data_center":
      source = "Bitbucket Data Center HTTP access token"
      break
  }
  if (orgConfigured) {
    return `Access through the ${source}, set up by your organization.`
  }
  return `Access uses your organization's ${source}.`
}

function HostSegment({
  provider,
  host,
  candidates,
  onChange,
}: {
  provider: VcsProvider
  host: string | undefined
  candidates: ConfiguredGitProvider[] | undefined
  onChange: (provider: VcsProvider) => void
}) {
  const content = (
    <>
      <VcsProviderLogo provider={provider} className="size-3.5" />
      <span className="truncate">
        {host ? `${host}/` : GIT_PROVIDER_LABELS[provider]}
      </span>
    </>
  )
  const className =
    "flex h-full max-w-[45%] shrink-0 items-center gap-1.5 border-r bg-muted/50 px-3 font-mono text-xs text-muted-foreground"
  if (!candidates) {
    return <span className={className}>{content}</span>
  }
  return (
    <DropdownMenu>
      <DropdownMenuTrigger asChild>
        <button
          type="button"
          aria-label={`Provider: ${GIT_PROVIDER_LABELS[provider]}. Change provider`}
          className={cn(
            className,
            "hover:bg-muted hover:text-foreground focus-visible:outline-none focus-visible:ring-1 focus-visible:ring-inset focus-visible:ring-ring"
          )}
        >
          {content}
          <ChevronDownIcon className="size-3 shrink-0" />
        </button>
      </DropdownMenuTrigger>
      <DropdownMenuContent align="start" className="min-w-56">
        {candidates.map((candidate) => (
          <DropdownMenuItem
            key={candidate.id}
            onSelect={() => onChange(candidate.id)}
            className="gap-2"
          >
            <VcsProviderLogo provider={candidate.id} className="size-3.5" />
            <span>{GIT_PROVIDER_LABELS[candidate.id]}</span>
            {candidate.host && (
              <span className="ml-auto font-mono text-xs text-muted-foreground">
                {candidate.host}
              </span>
            )}
          </DropdownMenuItem>
        ))}
      </DropdownMenuContent>
    </DropdownMenu>
  )
}

function ProviderCards({
  providers,
  onSelect,
}: {
  providers: ConfiguredGitProvider[]
  onSelect: (provider: VcsProvider) => void
}) {
  return (
    <div className="space-y-3">
      <p className="text-sm font-medium">Where is the repository?</p>
      <div className="grid gap-2 sm:grid-cols-2">
        {providers.map((entry) => (
          <button
            key={entry.id}
            type="button"
            onClick={() => onSelect(entry.id)}
            className="flex items-center gap-3 rounded-lg border px-3 py-3 text-left hover:bg-muted/50 focus-visible:outline-none focus-visible:ring-1 focus-visible:ring-inset focus-visible:ring-ring"
          >
            <VcsProviderLogo provider={entry.id} className="size-5" />
            <span className="min-w-0 space-y-0.5">
              <span className="block text-sm font-medium">
                {GIT_PROVIDER_LABELS[entry.id]}
              </span>
              <span className="block truncate font-mono text-xs text-muted-foreground">
                {entry.host ?? DEFAULT_GIT_HOSTS[entry.id] ?? "Host not set"}
              </span>
            </span>
          </button>
        ))}
      </div>
    </div>
  )
}

function AppRepositoryList({
  repositories,
  isLoading,
  hasError,
  filter,
  onSelect,
}: {
  repositories: GitHubAppRepository[]
  isLoading: boolean
  hasError: boolean
  filter: string
  onSelect: (repository: GitHubAppRepository) => void
}) {
  const query = filter.toLowerCase()
  const matches = repositories.filter((repository) =>
    repository.full_name.toLowerCase().includes(query)
  )

  let content: ReactNode
  if (isLoading) {
    content = (
      <div className="space-y-2" aria-label="Loading repositories">
        <Skeleton className="h-9 w-full" />
        <Skeleton className="h-9 w-full" />
        <Skeleton className="h-9 w-full" />
      </div>
    )
  } else if (hasError) {
    content = (
      <p className="text-sm text-muted-foreground">
        Couldn&apos;t load the list. A pasted repository URL still works.
      </p>
    )
  } else if (matches.length === 0) {
    content = (
      <p className="text-sm text-muted-foreground">
        No match. Paste the full URL to check it.
      </p>
    )
  } else {
    content = (
      <ul className="max-h-80 divide-y overflow-auto rounded-lg border">
        {matches.map((repository) => (
          <li key={`${repository.installation_id}:${repository.id}`}>
            <button
              type="button"
              onClick={() => onSelect(repository)}
              className="flex w-full items-center justify-between gap-3 px-3 py-2 text-left hover:bg-muted/50 focus-visible:outline-none focus-visible:ring-1 focus-visible:ring-inset focus-visible:ring-ring"
            >
              <span className="truncate font-mono text-sm">
                {repository.full_name}
              </span>
              <RepositoryVisibility isPrivate={repository.private} />
            </button>
          </li>
        ))}
      </ul>
    )
  }

  return (
    <div className="space-y-3">
      <p className="text-sm font-medium">
        Repositories the GitHub App can reach
      </p>
      {content}
    </div>
  )
}

function RepositoryVisibility({ isPrivate }: { isPrivate: boolean }) {
  if (isPrivate) {
    return (
      <span className="flex shrink-0 items-center gap-1 text-xs text-muted-foreground">
        <LockIcon className="size-3" />
        Private
      </span>
    )
  }
  return (
    <span className="flex shrink-0 items-center gap-1 text-xs text-muted-foreground">
      <GlobeIcon className="size-3" />
      Public
    </span>
  )
}

/** Read-only connection sheet with change, disconnect and back actions. */
function ConnectedRepository({
  workspace,
  canManageConnection,
  isEditing,
  onEditingChange,
}: {
  workspace: WorkspaceRead
  canManageConnection: boolean
  isEditing: boolean
  onEditingChange: (isEditing: boolean) => void
}) {
  const gitRepoUrl = workspace.settings?.git_repo_url || undefined
  const repoName = getRepoDisplayName(gitRepoUrl)
  const [confirmOpen, setConfirmOpen] = useState(false)
  const queryClient = useQueryClient()
  const { updateWorkspace, isUpdating } = useWorkspaceSettings(workspace.id)

  async function handleDisconnect() {
    try {
      // Only this key: `settings: null` would wipe every workspace setting.
      await updateWorkspace({ settings: { git_repo_url: null } })
    } catch {
      // useWorkspaceSettings reports the failure with a toast.
      return
    }
    // useWorkspaceSettings already invalidates the workspace query.
    await Promise.all(
      SYNC_QUERY_KEYS.map((key) =>
        queryClient.invalidateQueries({ queryKey: [key, workspace.id] })
      )
    )
  }

  return (
    <div className="space-y-6">
      <ConnectionSheet
        workspace={workspace}
        canManageConnection={canManageConnection}
      />
      <div className="flex items-center gap-2">
        {canManageConnection && isEditing && (
          <Button
            type="button"
            variant="ghost"
            size="sm"
            onClick={() => onEditingChange(false)}
          >
            Cancel
          </Button>
        )}
        {canManageConnection && !isEditing && (
          <Button
            type="button"
            variant="outline"
            size="sm"
            className="gap-1.5"
            onClick={() => onEditingChange(true)}
          >
            <PencilIcon className="size-3.5" />
            Change repository
          </Button>
        )}
        {canManageConnection && (
          <Button
            type="button"
            variant="ghost"
            size="sm"
            className="text-destructive hover:bg-destructive/10 hover:text-destructive"
            disabled={isUpdating}
            onClick={() => setConfirmOpen(true)}
          >
            Disconnect
          </Button>
        )}
      </div>
      <AlertDialog open={confirmOpen} onOpenChange={setConfirmOpen}>
        <AlertDialogContent>
          <AlertDialogHeader>
            <AlertDialogTitle>
              Disconnect {repoName ?? gitRepoUrl}?
            </AlertDialogTitle>
            <AlertDialogDescription>
              Push and pull stop until a repository is connected again. Nothing
              in this workspace or the repository is deleted.
            </AlertDialogDescription>
          </AlertDialogHeader>
          <AlertDialogFooter>
            <AlertDialogCancel>Cancel</AlertDialogCancel>
            <AlertDialogAction
              variant="destructive"
              onClick={() => void handleDisconnect()}
            >
              Disconnect
            </AlertDialogAction>
          </AlertDialogFooter>
        </AlertDialogContent>
      </AlertDialog>
    </div>
  )
}

/** Provider, repository, branch and latest commit as rows. */
function ConnectionSheet({
  workspace,
  canManageConnection,
}: {
  workspace: WorkspaceRead
  canManageConnection: boolean
}) {
  const gitRepoUrl = workspace.settings?.git_repo_url || undefined
  const provider = workspace.settings?.git_provider ?? "github"
  const repoName = getRepoDisplayName(gitRepoUrl)
  const host = getGitSshHost(gitRepoUrl)
  const { repositories = [] } = useGitHubAppRepositories(workspace.id, {
    enabled: provider === "github" && Boolean(gitRepoUrl),
  })
  const { branches, branchesIsLoading, branchesError } = useRepositoryBranches(
    workspace.id,
    {
      enabled: Boolean(gitRepoUrl),
      gitRepoUrl,
      provider,
      limit: 200,
    }
  )
  const baseBranch = getWorkspaceSyncBaseBranch(gitRepoUrl, branches)
  const { commits, commitsIsLoading } = useRepositoryCommits(workspace.id, {
    branch: baseBranch,
    gitRepoUrl,
    provider,
    limit: 20,
    enabled: Boolean(gitRepoUrl) && Boolean(baseBranch),
  })

  return (
    <dl className="divide-y border-y text-sm">
      <SheetRow
        icon={<VcsProviderIcon provider={provider} size="sm" />}
        label="Provider"
      >
        {getProviderSource(provider, host, repositories)}
      </SheetRow>
      <SheetRow icon={<BookMarkedIcon className="size-4" />} label="Repository">
        <span className="font-mono">{repoName ?? gitRepoUrl}</span>
      </SheetRow>
      <SheetRow icon={<GitBranchIcon className="size-4" />} label="Branch">
        {canManageConnection && gitRepoUrl && !branchesError ? (
          <BranchSelect
            workspaceId={workspace.id}
            gitRepoUrl={gitRepoUrl}
            branches={branches}
            isLoading={branchesIsLoading}
          />
        ) : (
          <BranchValue
            isLoading={branchesIsLoading}
            hasError={Boolean(branchesError)}
            branch={baseBranch}
          />
        )}
      </SheetRow>
      <SheetRow
        icon={<GitCommitHorizontalIcon className="size-4" />}
        label="Latest commit"
      >
        <LatestCommit
          isLoading={commitsIsLoading || branchesIsLoading}
          commit={commits?.[0]}
        />
      </SheetRow>
    </dl>
  )
}

function SheetRow({
  icon,
  label,
  children,
}: {
  icon: ReactNode
  label: string
  children: ReactNode
}) {
  return (
    <div className="grid grid-cols-[160px_minmax(0,1fr)] items-center gap-4 py-3">
      <dt className="flex items-center gap-2.5 text-muted-foreground">
        <span className="flex size-5 items-center justify-center">{icon}</span>
        {label}
      </dt>
      <dd className="min-w-0 truncate">{children}</dd>
    </div>
  )
}

function getProviderSource(
  provider: VcsProvider,
  host: string | undefined,
  repositories: GitHubAppRepository[]
) {
  if (provider === "github") {
    const accounts = new Set(
      repositories.map((repository) => repository.installation_account)
    )
    const [account] = accounts
    if (accounts.size === 1 && account) {
      return `GitHub App · installed on ${account}`
    }
    return "GitHub App"
  }
  const label = GIT_PROVIDER_LABELS[provider]
  if (provider === "bitbucket" || !host) {
    return label
  }
  return `${label} · ${host}`
}

const DEFAULT_BRANCH_VALUE = "__default_branch__"

/**
 * Branch this workspace syncs with: the repository default, or a pinned
 * branch stored as the `@ref` on the Git URL.
 */
function BranchSelect({
  workspaceId,
  gitRepoUrl,
  branches,
  isLoading,
}: {
  workspaceId: string
  gitRepoUrl: string
  branches: GitBranchInfo[] | undefined
  isLoading: boolean
}) {
  const queryClient = useQueryClient()
  const { updateWorkspace, isUpdating } = useWorkspaceSettings(workspaceId)
  const configuredRef = getWorkspaceSyncConfiguredRef(gitRepoUrl)
  const defaultBranch = getWorkspaceSyncDefaultBranch(branches)
  const otherBranches = (branches ?? []).filter((branch) => !branch.is_default)
  // Keep a pinned branch selectable even if it no longer exists remotely.
  if (
    configuredRef &&
    configuredRef !== defaultBranch &&
    !otherBranches.some((branch) => branch.name === configuredRef)
  ) {
    otherBranches.unshift({ name: configuredRef, is_default: false })
  }

  async function handleChange(value: string) {
    const ref = value === DEFAULT_BRANCH_VALUE ? undefined : value
    if (ref === configuredRef) {
      return
    }
    try {
      await updateWorkspace({
        settings: {
          git_repo_url: withWorkspaceSyncConfiguredRef(gitRepoUrl, ref),
        },
      })
    } catch {
      // useWorkspaceSettings reports the failure with a toast.
      return
    }
    await Promise.all(
      SYNC_QUERY_KEYS.map((key) =>
        queryClient.invalidateQueries({ queryKey: [key, workspaceId] })
      )
    )
  }

  if (isLoading) {
    return <Skeleton className="h-4 w-20 rounded-sm" />
  }

  return (
    <Select
      value={
        configuredRef && configuredRef !== defaultBranch
          ? configuredRef
          : DEFAULT_BRANCH_VALUE
      }
      onValueChange={(value) => void handleChange(value)}
      disabled={isUpdating}
    >
      <SelectTrigger
        aria-label="Branch"
        className="h-8 w-auto min-w-44 max-w-full gap-2 font-mono text-sm"
      >
        <SelectValue />
      </SelectTrigger>
      <SelectContent>
        <SelectItem value={DEFAULT_BRANCH_VALUE}>
          <span className="font-mono">{defaultBranch ?? "Default"}</span>
          <span className="ml-2 font-sans text-xs text-muted-foreground">
            default
          </span>
        </SelectItem>
        {otherBranches.map((branch) => (
          <SelectItem
            key={branch.name}
            value={branch.name}
            className="font-mono"
          >
            {branch.name}
          </SelectItem>
        ))}
      </SelectContent>
    </Select>
  )
}

function BranchValue({
  isLoading,
  hasError,
  branch,
}: {
  isLoading: boolean
  hasError: boolean
  branch: string | undefined
}) {
  if (isLoading) {
    return <Skeleton className="h-4 w-20 rounded-sm" />
  }
  if (hasError) {
    return (
      <span className="flex items-center gap-1.5 text-destructive">
        <AlertTriangleIcon className="size-3.5" />
        Could not reach repository
      </span>
    )
  }
  return <span className="font-mono">{branch ?? "Unknown"}</span>
}

function LatestCommit({
  isLoading,
  commit,
}: {
  isLoading: boolean
  commit:
    | { sha: string; message: string; author: string; date?: string | null }
    | undefined
}) {
  if (isLoading) {
    return <Skeleton className="h-4 w-48 rounded-sm" />
  }
  if (!commit) {
    return <span className="text-muted-foreground">No commits yet</span>
  }
  // Who and when say more about the repo's state than the message, which
  // stays available on hover.
  return (
    <span title={commit.message.split("\n")[0]}>
      <span className="font-mono text-[13px]">
        {commit.sha.substring(0, 7)}
      </span>
      {` · ${commit.author || "Unknown author"}`}
      {commit.date && ` · ${getRelativeTime(new Date(commit.date))}`}
    </span>
  )
}
