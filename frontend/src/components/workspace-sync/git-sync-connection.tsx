"use client"

import {
  AlertCircleIcon,
  AlertTriangleIcon,
  ArrowLeftIcon,
  ArrowUpRight,
  BookMarkedIcon,
  CheckIcon,
  GitBranchIcon,
  GitCommitHorizontalIcon,
  GlobeIcon,
  LockIcon,
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
import { Input } from "@/components/ui/input"
import { Label } from "@/components/ui/label"
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

/** Short name of the organization credential each provider connects with. */
const ACCESS_SOURCES: Record<VcsProvider, string> = {
  github: "GitHub App",
  gitlab: "GitLab token",
  bitbucket: "Bitbucket token",
  bitbucket_data_center: "Bitbucket Data Center token",
}

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
  /** Loads branches and commits, which need sync access. Defaults to true. */
  canSync?: boolean
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
  canSync = true,
  onBack,
  onSaved,
}: GitSyncConnectionPanelProps) {
  const isConnected = Boolean(workspace.settings?.git_repo_url)
  const provider = workspace.settings?.git_provider ?? "github"
  const showRemoteLine = canManageConnection && !isConnected
  const providers = useGitSyncProviders({ enabled: showRemoteLine })
  const canManageOrgSettings = useScopeCheck("org:settings:update") === true

  let body: ReactNode = null
  if (!showRemoteLine) {
    // Connected: the repository sheet is the whole body.
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
        onSaved={() => onSaved?.()}
      />
    )
  }

  return (
    <div className="min-h-0 flex-1 overflow-auto">
      <div className="mx-auto w-full max-w-[620px] space-y-8 px-6 pb-16 pt-16 md:pt-24">
        {onBack && (
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
              ? `Shared by everyone in this workspace. Access uses the organization's ${ACCESS_SOURCES[provider]}.`
              : SUBTITLE}
          </p>
        </div>
        {isConnected && (
          <ConnectedRepository
            workspace={workspace}
            canManageConnection={canManageConnection}
            canSync={canSync}
            onSaved={onSaved}
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

/** Provider choice, repository field, and repository list for one workspace. */
function GitSyncConnect({
  workspace,
  providers,
  canManageOrgSettings,
  onSaved,
}: GitSyncConnectProps) {
  const persistedProvider = workspace.settings?.git_provider ?? undefined
  const candidates = getCandidateProviders(
    providers,
    persistedProvider,
    undefined
  )
  const [provider, setProvider] = useState<VcsProvider | undefined>(() =>
    getInitialProvider(providers, persistedProvider, undefined)
  )
  const [input, setInput] = useState("")
  const [hasAttempted, setHasAttempted] = useState(false)
  const { updateWorkspace, isUpdating } = useWorkspaceSettings(workspace.id)
  const repository = useRepositoryInput({
    workspaceId: workspace.id,
    provider,
    candidates,
    orgConfigured: providers.kind === "known",
    canManageOrgSettings,
    input,
  })
  const { resolution } = repository
  const error =
    hasAttempted && resolution.kind === "invalid"
      ? resolution.message
      : undefined

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

  return (
    <div className="space-y-8">
      <form onSubmit={handleSubmit} className="space-y-5">
        <div className="space-y-1.5">
          <Label htmlFor="git-sync-provider">Provider</Label>
          <ProviderSelect
            id="git-sync-provider"
            candidates={candidates}
            provider={provider}
            onChange={(id) => {
              setProvider(id)
              setHasAttempted(false)
            }}
          />
        </div>
        <div className="space-y-1.5">
          <Label htmlFor="git-sync-repository">Repository</Label>
          <RepositoryField
            id="git-sync-repository"
            provider={provider}
            host={repository.target?.host}
            value={input}
            parsed={repository.parsed}
            error={error}
            disabled={!provider}
            onChange={setInput}
          />
        </div>
        <div className="flex items-center gap-3">
          <Button
            type="submit"
            className="h-9 shrink-0 px-4"
            disabled={
              isUpdating || !provider || repository.parsed.kind === "empty"
            }
          >
            {isUpdating ? "Connecting..." : "Connect"}
          </Button>
          {provider && (
            <span className="text-xs text-muted-foreground">
              {getAccessLine(provider, repository.appRepositories)}
            </span>
          )}
        </div>
      </form>
      {provider === "github" && (
        <div className="border-t pt-6">
          <AppRepositoryList
            repositories={repository.appRepositories}
            isLoading={repository.repositoriesIsLoading}
            hasError={repository.repositoriesHasError}
            filter={
              repository.parsed.kind === "ok"
                ? repository.parsed.remote.path
                : input.trim()
            }
            onSelect={(appRepository) => {
              setInput(appRepository.full_name)
              setHasAttempted(false)
            }}
          />
        </div>
      )}
    </div>
  )
}

/**
 * Parses typed repository input for one provider and resolves the URL to
 * save, checking GitHub input against the app's repositories.
 */
function useRepositoryInput({
  workspaceId,
  provider,
  candidates,
  orgConfigured,
  canManageOrgSettings,
  input,
}: {
  workspaceId: string
  provider: VcsProvider | undefined
  candidates: ConfiguredGitProvider[]
  orgConfigured: boolean
  canManageOrgSettings: boolean
  input: string
}) {
  const { repositories, repositoriesIsLoading, repositoriesError } =
    useGitHubAppRepositories(workspaceId, { enabled: provider === "github" })
  const appRepositories = repositories ?? []
  const repositoriesLoaded = Boolean(repositories) && !repositoriesError
  const repositoriesHasError =
    Boolean(repositoriesError) && !repositories?.length

  if (!provider) {
    return {
      target: undefined,
      parsed: { kind: "empty" } as GitRemoteParseResult,
      resolution: { kind: "empty" } as ConnectionResolution,
      appRepositories,
      repositoriesIsLoading,
      repositoriesHasError,
    }
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
  return {
    target,
    parsed,
    resolution,
    appRepositories,
    repositoriesIsLoading,
    repositoriesHasError,
  }
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

function getPlaceholder(provider: VcsProvider) {
  switch (provider) {
    case "github":
      return "owner/repository"
    case "gitlab":
      return "group/project"
    case "bitbucket":
      return "workspace/repository"
    case "bitbucket_data_center":
      return "https://bitbucket.example.com/projects/PROJECT/repos/repository"
  }
}

function getProviderHint(provider: VcsProvider, host: string | undefined) {
  // A bare path needs a known host, so without one only a full URL works.
  if (!host) {
    return `Paste the full repository URL from ${GIT_PROVIDER_LABELS[provider]}.`
  }
  const source = host
  switch (provider) {
    case "gitlab":
      return `Paste the project URL from ${source}, or type group/project. Nested groups work.`
    case "bitbucket_data_center":
      return `Paste the repository URL from ${source}.`
    default:
      return `Paste the repository URL from ${source}, or type ${getPlaceholder(provider)}.`
  }
}

/** Access source for the provider, claiming only what the data supports. */
function getAccessLine(
  provider: VcsProvider,
  repositories: GitHubAppRepository[]
) {
  if (provider === "github") {
    const accounts = new Set(
      repositories.map((repository) => repository.installation_account)
    )
    const [account] = accounts
    if (accounts.size === 1 && account) {
      return `Uses the GitHub App on ${account}.`
    }
  }
  return `Uses the organization's ${ACCESS_SOURCES[provider]}.`
}

/** Provider for the connection; read-only when only one is set up. */
function ProviderSelect({
  id,
  candidates,
  provider,
  onChange,
}: {
  id: string
  candidates: ConfiguredGitProvider[]
  provider: VcsProvider | undefined
  onChange: (provider: VcsProvider) => void
}) {
  return (
    <Select
      value={provider}
      onValueChange={(value) => onChange(value as VcsProvider)}
      disabled={candidates.length <= 1}
    >
      <SelectTrigger
        id={id}
        className="h-10 [&>span]:flex [&>span]:items-center [&>span]:gap-2"
      >
        <SelectValue placeholder="Choose where the repository lives" />
      </SelectTrigger>
      <SelectContent>
        {candidates.map((candidate) => (
          <SelectItem key={candidate.id} value={candidate.id}>
            <span className="flex items-center gap-2">
              <VcsProviderLogo provider={candidate.id} className="size-3.5" />
              <span>{GIT_PROVIDER_LABELS[candidate.id]}</span>
              {candidate.host && (
                <span className="font-mono text-xs text-muted-foreground">
                  {candidate.host}
                </span>
              )}
            </span>
          </SelectItem>
        ))}
      </SelectContent>
    </Select>
  )
}

/**
 * Repository input with one line under it: the parse error, what will be
 * saved, or how to fill it in.
 */
function RepositoryField({
  id,
  provider,
  host,
  value,
  parsed,
  error,
  disabled,
  autoFocus,
  onChange,
}: {
  id: string
  provider: VcsProvider | undefined
  host: string | undefined
  value: string
  parsed: GitRemoteParseResult
  error: string | undefined
  disabled?: boolean
  autoFocus?: boolean
  onChange: (value: string) => void
}) {
  let status: ReactNode = null
  if (error) {
    status = (
      <p role="alert" className="flex items-start gap-1.5 text-destructive">
        <AlertCircleIcon className="mt-px size-3.5 shrink-0" />
        <span>{error}</span>
      </p>
    )
  } else if (parsed.kind === "ok") {
    status = (
      <p className="flex items-center gap-1.5 text-muted-foreground">
        <CheckIcon className="size-3.5 shrink-0 text-green-700 dark:text-green-500" />
        <span>
          <code className="font-mono text-foreground">
            {parsed.remote.path}
          </code>{" "}
          on {parsed.remote.host}
          {parsed.remote.ref && ` at ${parsed.remote.ref}`}
        </span>
      </p>
    )
  } else if (provider) {
    status = (
      <p className="text-muted-foreground">{getProviderHint(provider, host)}</p>
    )
  }
  return (
    <div className="space-y-1.5">
      <Input
        id={id}
        aria-label="Repository"
        aria-invalid={Boolean(error)}
        className={cn(
          "h-10 font-mono",
          error && "border-destructive focus-visible:ring-destructive"
        )}
        placeholder={provider ? getPlaceholder(provider) : undefined}
        value={value}
        disabled={disabled}
        autoFocus={autoFocus}
        onFocus={(event) => event.currentTarget.select()}
        onChange={(event) => onChange(event.target.value)}
        autoComplete="off"
        spellCheck={false}
      />
      <div className="text-xs">{status}</div>
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

/** Connection sheet with the repository editable in place, then disconnect. */
function ConnectedRepository({
  workspace,
  canManageConnection,
  canSync,
  onSaved,
}: {
  workspace: WorkspaceRead
  canManageConnection: boolean
  canSync: boolean
  onSaved?: () => void
}) {
  const gitRepoUrl = workspace.settings?.git_repo_url || undefined
  const repoName = getRepoDisplayName(gitRepoUrl)
  const [confirmOpen, setConfirmOpen] = useState(false)
  const [isEditingRepository, setIsEditingRepository] = useState(false)
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
        canSync={canSync}
        repositoryEditor={
          isEditingRepository ? (
            <RepositoryEditor
              workspace={workspace}
              onDone={() => setIsEditingRepository(false)}
              onSaved={onSaved}
            />
          ) : undefined
        }
        onEditRepository={
          canManageConnection ? () => setIsEditingRepository(true) : undefined
        }
      />
      {canManageConnection && (
        <div className="flex items-center gap-3">
          <Button
            type="button"
            variant="outline"
            size="sm"
            className="text-destructive hover:bg-destructive/10 hover:text-destructive"
            disabled={isUpdating}
            onClick={() => setConfirmOpen(true)}
          >
            Disconnect
          </Button>
          <span className="text-xs text-muted-foreground">
            Stops push and pull for this workspace.
          </span>
        </div>
      )}
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

/**
 * Inline editor for the repository row. The provider stays as connected; the
 * field starts on the current repository, selected so a paste replaces it.
 */
function RepositoryEditor({
  workspace,
  onDone,
  onSaved,
}: {
  workspace: WorkspaceRead
  onDone: () => void
  onSaved?: () => void
}) {
  const persistedUrl = workspace.settings?.git_repo_url || undefined
  const provider = workspace.settings?.git_provider ?? "github"
  const providers = useGitSyncProviders({ enabled: true })
  const canManageOrgSettings = useScopeCheck("org:settings:update") === true
  const knownProviders =
    providers.kind === "loading" ? ({ kind: "unknown" } as const) : providers
  const candidates = getCandidateProviders(
    knownProviders,
    provider,
    persistedUrl
  )
  const [input, setInput] = useState(() =>
    getPrefill(persistedUrl, provider, candidates)
  )
  const [hasAttempted, setHasAttempted] = useState(false)
  const queryClient = useQueryClient()
  const { updateWorkspace, isUpdating } = useWorkspaceSettings(workspace.id)
  const repository = useRepositoryInput({
    workspaceId: workspace.id,
    provider,
    candidates,
    orgConfigured: providers.kind === "known",
    canManageOrgSettings,
    input,
  })
  const { resolution } = repository
  const error =
    hasAttempted && resolution.kind === "invalid"
      ? resolution.message
      : undefined
  const isUnchanged =
    resolution.kind === "ok" && resolution.gitUrl === persistedUrl

  async function handleSubmit(event: FormEvent<HTMLFormElement>) {
    event.preventDefault()
    setHasAttempted(true)
    if (resolution.kind !== "ok") {
      return
    }
    if (isUnchanged) {
      onDone()
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
    await Promise.all(
      SYNC_QUERY_KEYS.map((key) =>
        queryClient.invalidateQueries({ queryKey: [key, workspace.id] })
      )
    )
    onSaved?.()
    onDone()
  }

  if (providers.kind === "loading") {
    return <Skeleton className="h-10 w-full rounded-md" />
  }

  return (
    <form onSubmit={handleSubmit} className="space-y-2.5 whitespace-normal">
      <RepositoryField
        id="git-sync-repository"
        provider={provider}
        host={repository.target?.host}
        value={input}
        parsed={repository.parsed}
        error={error}
        autoFocus
        onChange={setInput}
      />
      <p className="text-xs text-muted-foreground">
        Changing the repository clears any preview on this page.
      </p>
      <div className="flex gap-2">
        <Button
          type="submit"
          size="sm"
          disabled={isUpdating || repository.parsed.kind === "empty"}
        >
          {isUpdating ? "Saving..." : "Save"}
        </Button>
        <Button type="button" variant="outline" size="sm" onClick={onDone}>
          Cancel
        </Button>
      </div>
    </form>
  )
}

function ConnectionSheet({
  workspace,
  canManageConnection,
  canSync,
  repositoryEditor,
  onEditRepository,
}: {
  workspace: WorkspaceRead
  canManageConnection: boolean
  canSync: boolean
  /** Replaces the repository value while it is being edited. */
  repositoryEditor?: ReactNode
  /** Shows "Change" on the repository row when set. */
  onEditRepository?: () => void
}) {
  const gitRepoUrl = workspace.settings?.git_repo_url || undefined
  const provider = workspace.settings?.git_provider ?? "github"
  const repoName = getRepoDisplayName(gitRepoUrl)
  const host = getGitSshHost(gitRepoUrl)
  const { repositories = [] } = useGitHubAppRepositories(workspace.id, {
    enabled:
      canManageConnection && provider === "github" && Boolean(gitRepoUrl),
  })
  // Branch and commit routes need sync access, not connection access.
  const { branches, branchesIsLoading, branchesError } = useRepositoryBranches(
    workspace.id,
    {
      enabled: canSync && Boolean(gitRepoUrl),
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
    enabled: canSync && Boolean(gitRepoUrl) && Boolean(baseBranch),
  })

  function renderBranch() {
    if (!canSync) {
      return (
        <span className="font-mono">
          {getWorkspaceSyncConfiguredRef(gitRepoUrl) ?? "Repository default"}
        </span>
      )
    }
    if (canManageConnection && gitRepoUrl && !branchesError) {
      return (
        <BranchSelect
          workspaceId={workspace.id}
          gitRepoUrl={gitRepoUrl}
          branches={branches}
          isLoading={branchesIsLoading}
        />
      )
    }
    return (
      <BranchValue
        isLoading={branchesIsLoading}
        hasError={Boolean(branchesError)}
        branch={baseBranch}
      />
    )
  }

  return (
    <dl className="divide-y border-y text-sm">
      <SheetRow
        icon={<VcsProviderIcon provider={provider} size="sm" />}
        label="Provider"
      >
        {getProviderSource(provider, host, repositories)}
      </SheetRow>
      <SheetRow
        icon={<BookMarkedIcon className="size-4" />}
        label="Repository"
        alignTop={Boolean(repositoryEditor)}
      >
        {repositoryEditor ?? (
          <span className="flex items-center gap-3">
            <span className="min-w-0 flex-1 truncate font-mono">
              {repoName ?? gitRepoUrl}
            </span>
            {onEditRepository && (
              <button
                type="button"
                className="shrink-0 text-xs text-muted-foreground underline underline-offset-2 hover:text-foreground"
                onClick={onEditRepository}
              >
                Change
              </button>
            )}
          </span>
        )}
      </SheetRow>
      <SheetRow icon={<GitBranchIcon className="size-4" />} label="Branch">
        {renderBranch()}
      </SheetRow>
      {canSync && (
        <SheetRow
          icon={<GitCommitHorizontalIcon className="size-4" />}
          label="Latest commit"
        >
          <LatestCommit
            isLoading={commitsIsLoading || branchesIsLoading}
            commit={commits?.[0]}
          />
        </SheetRow>
      )}
    </dl>
  )
}

function SheetRow({
  icon,
  label,
  alignTop = false,
  children,
}: {
  icon: ReactNode
  label: string
  /** Pins the label to the top when the value spans several lines. */
  alignTop?: boolean
  children: ReactNode
}) {
  return (
    <div
      className={cn(
        "grid grid-cols-[160px_minmax(0,1fr)] gap-4 py-3",
        alignTop ? "items-start" : "items-center"
      )}
    >
      <dt
        className={cn(
          "flex items-center gap-2.5 text-muted-foreground",
          alignTop && "h-10"
        )}
      >
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
