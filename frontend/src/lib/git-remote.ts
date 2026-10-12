import type { GitHubAppRepository, VcsProvider } from "@/client"
import { GIT_SSH_URL_REGEX } from "@/lib/git"

/** Display names for workspace Git sync providers. */
export const GIT_PROVIDER_LABELS: Record<VcsProvider, string> = {
  github: "GitHub",
  gitlab: "GitLab",
  bitbucket: "Bitbucket Cloud",
  bitbucket_data_center: "Bitbucket Data Center",
}

/** Hosts used when a provider has a fixed public host. */
export const DEFAULT_GIT_HOSTS: Partial<Record<VcsProvider, string>> = {
  github: "github.com",
  bitbucket: "bitbucket.org",
}

/** Where pasted repository references are resolved for one provider. */
export interface GitRemoteTarget {
  provider: VcsProvider
  /** Host the provider is set up on; undefined when unknown. */
  host?: string
  /** True when `host` is authoritative, so other hosts are rejected. */
  hostIsKnown: boolean
  /** True when the host comes from the organization's provider setup. */
  orgConfigured: boolean
}

/** A repository reference resolved to the git+ssh URL the workspace stores. */
export interface GitRemote {
  gitUrl: string
  host: string
  /** Repository path without `.git`, e.g. `TracecatHQ/detections`. */
  path: string
  ref?: string
}

/** Outcome of parsing the remote line input. */
export type GitRemoteParseResult =
  | { kind: "empty" }
  | { kind: "invalid"; message: string }
  | { kind: "ok"; remote: GitRemote }

const SSH_URL_REGEX =
  /^(?:git\+)?ssh:\/\/(?:(?<user>[^/@:]+)@)?(?<host>[^/:@]+)(?::(?<port>\d+))?\/(?<rest>.+)$/
const SCP_REGEX = /^(?<user>[^@/\s:]+)@(?<host>[^:/\s]+):(?<rest>[^\s]+)$/
const PATH_SEGMENT_REGEX = /^[\w.~-]+$/

interface RawRemote {
  user: string
  host: string
  port?: string
  segments: string[]
  ref?: string
}

/**
 * Parse an SSH clone URL into the git+ssh URL a workspace stores.
 *
 * Accepts scp-style `git@host:owner/repo.git` and `git+ssh://` or `ssh://`
 * URLs. Web URLs are rejected: only SSH remotes are supported.
 */
export function parseGitRemote(
  input: string,
  target: GitRemoteTarget
): GitRemoteParseResult {
  const value = input.trim()
  if (!value) {
    return { kind: "empty" }
  }
  const raw = toRawRemote(value)
  if (!raw) {
    return { kind: "invalid", message: describeExpectedInput(target) }
  }
  return validateRawRemote(raw, target)
}

function toRawRemote(value: string): RawRemote | undefined {
  const ssh = SSH_URL_REGEX.exec(value)?.groups
  if (ssh) {
    return splitSshPath({
      user: ssh.user ?? "git",
      host: ssh.host,
      port: ssh.port,
      rest: ssh.rest,
    })
  }
  const scp = SCP_REGEX.exec(value)?.groups
  if (scp && !value.includes("://")) {
    return splitSshPath({ user: scp.user, host: scp.host, rest: scp.rest })
  }
  return undefined
}

function splitSshPath({
  user,
  host,
  port,
  rest,
}: {
  user: string
  host: string
  port?: string
  rest: string
}): RawRemote {
  const [path, ...refParts] = rest.split("@")
  const ref = refParts.join("@") || undefined
  return {
    user,
    host: normalizeHost(host),
    port,
    segments: stripGitSuffix(path).split("/").filter(Boolean),
    ref,
  }
}

function validateRawRemote(
  raw: RawRemote,
  target: GitRemoteTarget
): GitRemoteParseResult {
  const label = GIT_PROVIDER_LABELS[target.provider]
  // Data Center SSH included: its transport requires the instance host exactly.
  if (
    target.hostIsKnown &&
    target.host &&
    !hostMatchesTarget(raw.host, target)
  ) {
    return {
      kind: "invalid",
      message: target.orgConfigured
        ? `${raw.host} isn't set up for this organization. ${label} here is ${target.host}.`
        : `${label} repositories here are on ${target.host}, not ${raw.host}.`,
    }
  }
  if (
    raw.port &&
    (target.provider === "bitbucket" ||
      target.provider === "bitbucket_data_center")
  ) {
    return {
      kind: "invalid",
      message:
        "Remove the port. Bitbucket takes it from the organization's instance URL.",
    }
  }
  const { segments } = raw
  const needsExactlyTwo = target.provider !== "gitlab"
  const hasValidSegments =
    segments.length >= 2 &&
    (!needsExactlyTwo || segments.length === 2) &&
    segments.every((segment) => PATH_SEGMENT_REGEX.test(segment))
  if (!hasValidSegments) {
    return { kind: "invalid", message: describeExpectedInput(target) }
  }
  const path = segments.join("/")
  const port = raw.port ? `:${raw.port}` : ""
  const ref = raw.ref ? `@${raw.ref}` : ""
  const gitUrl = `git+ssh://${raw.user}@${raw.host}${port}/${path}.git${ref}`
  if (!GIT_SSH_URL_REGEX.test(gitUrl)) {
    return { kind: "invalid", message: describeExpectedInput(target) }
  }
  return {
    kind: "ok",
    remote: { gitUrl, host: raw.host, path, ref: raw.ref },
  }
}

function describeExpectedInput(target: GitRemoteTarget): string {
  return `Paste the SSH clone URL, e.g. ${getSshCloneExample(target.provider, target.host)}.`
}

/** Example SSH clone URL for a provider, on its host when known. */
export function getSshCloneExample(
  provider: VcsProvider,
  host: string | undefined
): string {
  switch (provider) {
    case "github":
      return `git@${host ?? "github.com"}:owner/repository.git`
    case "gitlab":
      return `git@${host ?? "gitlab.example.com"}:group/project.git`
    case "bitbucket":
      return "git@bitbucket.org:workspace/repository.git"
    case "bitbucket_data_center":
      return `git@${host ?? "bitbucket.example.com"}:PROJECT/repository.git`
  }
}

/** GitLab also accepts SSH aliases on subdomains, e.g. altssh.gitlab.com. */
function hostMatchesTarget(host: string, target: GitRemoteTarget): boolean {
  if (host === target.host) {
    return true
  }
  return target.provider === "gitlab" && host.endsWith(`.${target.host}`)
}

/** Public hosts that also answer on `www.`; self-hosted names stay as set up. */
const WWW_ALIAS_HOSTS = new Set(["github.com", "gitlab.com", "bitbucket.org"])

function normalizeHost(host: string): string {
  const lower = host.toLowerCase()
  const bare = lower.replace(/^www\./, "")
  return WWW_ALIAS_HOSTS.has(bare) ? bare : lower
}

function stripGitSuffix(path: string): string {
  return path.replace(/\/+$/, "").replace(/\.git$/, "")
}

/**
 * Host of a stored git+ssh URL, or undefined when it does not parse.
 */
export function getGitSshHost(url: string | null | undefined) {
  if (!url) {
    return undefined
  }
  return GIT_SSH_URL_REGEX.exec(url)?.groups?.hostname?.toLowerCase()
}

/**
 * Hostname of a provider base URL such as `https://gitlab.example.com`.
 */
export function getBaseUrlHost(baseUrl: string | null | undefined) {
  if (!baseUrl) {
    return undefined
  }
  try {
    return normalizeHost(new URL(baseUrl).hostname)
  } catch {
    return undefined
  }
}

/**
 * Stored URL for a GitHub App repository, pinning a non-main default branch.
 */
export function getAppRepositoryGitUrl(repository: GitHubAppRepository) {
  const defaultBranch = repository.default_branch.trim()
  if (!defaultBranch || defaultBranch === "main") {
    return repository.git_url
  }
  return `${repository.git_url}@${defaultBranch}`
}

/**
 * GitHub App repository matching a parsed remote's host and path.
 */
export function findAppRepository(
  remote: GitRemote,
  repositories: GitHubAppRepository[]
) {
  const path = remote.path.toLowerCase()
  return repositories.find(
    (repository) =>
      repository.full_name.toLowerCase() === path &&
      getGitSshHost(repository.git_url) === remote.host
  )
}
