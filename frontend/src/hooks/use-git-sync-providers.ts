"use client"

import {
  type BitbucketDataCenterTokenCredentialsStatus,
  type BitbucketTokenCredentialsStatus,
  type GitHubAppCredentialsStatus,
  type GitLabTokenCredentialsStatus,
  type VcsProvider,
  vcsGetBitbucketDataCenterTokenCredentialsStatus,
  vcsGetBitbucketTokenCredentialsStatus,
  vcsGetGithubAppCredentialsStatus,
  vcsGetGitlabTokenCredentialsStatus,
} from "@/client"
import { useScopeCheck } from "@/components/auth/scope-guard"
import { getBaseUrlHost } from "@/lib/git-remote"
import { useQuery } from "@/lib/query"

/** A Git provider the organization has usable credentials for. */
export interface ConfiguredGitProvider {
  id: VcsProvider
  /** Web host from the provider setup; undefined for GitHub. */
  host?: string
}

/**
 * What this page can truthfully say about the organization's Git providers.
 * `unknown` means the viewer cannot read provider status.
 */
export type GitSyncProvidersState =
  | { kind: "loading" }
  | { kind: "known"; providers: ConfiguredGitProvider[] }
  | { kind: "unknown" }

interface ProviderStatuses {
  github?: GitHubAppCredentialsStatus
  gitlab?: GitLabTokenCredentialsStatus
  bitbucket?: BitbucketTokenCredentialsStatus
  bitbucketDataCenter?: BitbucketDataCenterTokenCredentialsStatus
}

function isUsable(status: { exists: boolean; is_corrupted?: boolean }) {
  return status.exists && !status.is_corrupted
}

/**
 * Providers with saved, uncorrupted credentials, in provider-list order.
 */
export function getConfiguredGitProviders(
  statuses: ProviderStatuses
): ConfiguredGitProvider[] {
  const providers: ConfiguredGitProvider[] = []
  if (statuses.github && isUsable(statuses.github)) {
    providers.push({ id: "github" })
  }
  if (statuses.gitlab && isUsable(statuses.gitlab)) {
    providers.push({
      id: "gitlab",
      host: getBaseUrlHost(statuses.gitlab.base_url),
    })
  }
  if (statuses.bitbucket && isUsable(statuses.bitbucket)) {
    providers.push({ id: "bitbucket", host: "bitbucket.org" })
  }
  if (statuses.bitbucketDataCenter && isUsable(statuses.bitbucketDataCenter)) {
    providers.push({
      id: "bitbucket_data_center",
      host: getBaseUrlHost(statuses.bitbucketDataCenter.base_url),
    })
  }
  return providers
}

const STATUS_QUERY_OPTIONS = {
  retry: false,
  meta: { suppressErrorToast: true },
} as const

/**
 * Resolve the organization's configured Git providers for the connect flow.
 * Provider status endpoints need org:settings:read, so other viewers get
 * `unknown` and must pick a provider explicitly.
 */
export function useGitSyncProviders(options?: {
  enabled?: boolean
}): GitSyncProvidersState {
  // Workspace admins without org:settings:read need a slim provider-status
  // read gated on workspace:update before this can stop returning unknown.
  const canReadOrgSettings = useScopeCheck("org:settings:read")
  const enabled = canReadOrgSettings === true && options?.enabled !== false

  const github = useQuery({
    queryKey: ["github-app-credentials-status"],
    queryFn: async () => await vcsGetGithubAppCredentialsStatus(),
    enabled,
    ...STATUS_QUERY_OPTIONS,
  })
  const gitlab = useQuery({
    queryKey: ["gitlab-token-credentials-status"],
    queryFn: async () => await vcsGetGitlabTokenCredentialsStatus(),
    enabled,
    ...STATUS_QUERY_OPTIONS,
  })
  const bitbucket = useQuery({
    queryKey: ["bitbucket-token-credentials-status"],
    queryFn: async () => await vcsGetBitbucketTokenCredentialsStatus(),
    enabled,
    ...STATUS_QUERY_OPTIONS,
  })
  const bitbucketDataCenter = useQuery({
    queryKey: ["bitbucket-data-center-token-credentials-status"],
    queryFn: async () =>
      await vcsGetBitbucketDataCenterTokenCredentialsStatus(),
    enabled,
    ...STATUS_QUERY_OPTIONS,
  })

  if (canReadOrgSettings === undefined || options?.enabled === false) {
    return { kind: "loading" }
  }
  if (!canReadOrgSettings) {
    return { kind: "unknown" }
  }
  const queries = [github, gitlab, bitbucket, bitbucketDataCenter]
  if (queries.some((query) => query.isError)) {
    return { kind: "unknown" }
  }
  if (queries.some((query) => query.isLoading)) {
    return { kind: "loading" }
  }
  return {
    kind: "known",
    providers: getConfiguredGitProviders({
      github: github.data,
      gitlab: gitlab.data,
      bitbucket: bitbucket.data,
      bitbucketDataCenter: bitbucketDataCenter.data,
    }),
  }
}
