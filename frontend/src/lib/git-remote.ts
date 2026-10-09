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
  /** Host used for bare `owner/repo` input; undefined when unknown. */
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
  host?: string
  port?: string
  segments: string[]
  ref?: string
}

/**
 * Parse remote line input into the git+ssh URL a workspace stores.
 *
 * Accepts https URLs, scp-style `git@host:owner/repo.git`, `git+ssh://` and
 * `ssh://` URLs, `host/owner/repo`, and bare `owner/repo` (resolved against
 * the target host). Web URLs drop UI suffixes such as `/tree/main`.
 */
export function parseGitRemote(
  input: string,
  target: GitRemoteTarget
): GitRemoteParseResult {
  const value = input.trim()
  if (!value) {
    return { kind: "empty" }
  }
  const raw = toRawRemote(value, target)
  if (!raw) {
    return { kind: "invalid", message: describeExpectedInput(target) }
  }
  return validateRawRemote(raw, target)
}

function toRawRemote(
  value: string,
  target: GitRemoteTarget
): RawRemote | undefined {
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
  if (/^https?:\/\//i.test(value)) {
    let url: URL
    try {
      url = new URL(value)
    } catch {
      return undefined
    }
    return {
      user: "git",
      host: normalizeHost(url.hostname),
      segments: webPathSegments(url.pathname, target.provider),
    }
  }
  if (value.includes("://") || /\s/.test(value)) {
    return undefined
  }
  const segments = value.split("/").filter(Boolean)
  const [first] = segments
  // GitLab group names may contain dots, so on a known GitLab host a dotted
  // first segment is a group, not another host.
  const dottedFirstIsGroup = target.provider === "gitlab" && target.hostIsKnown
  const firstIsHost =
    first !== undefined &&
    (normalizeHost(first) === target.host ||
      (segments.length >= 3 && !dottedFirstIsGroup && /[.:]/.test(first)))
  if (firstIsHost) {
    return {
      user: "git",
      host: normalizeHost(first.split(":")[0]),
      segments: webPathSegments(segments.slice(1).join("/"), target.provider),
    }
  }
  return {
    user: "git",
    host: target.host,
    segments: webPathSegments(value, target.provider),
  }
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

/** Repository path segments from a web URL path, without UI suffixes. */
function webPathSegments(pathname: string, provider: VcsProvider): string[] {
  const segments = pathname.split("/").filter(Boolean)
  switch (provider) {
    case "gitlab": {
      const uiIndex = segments.indexOf("-")
      const repoSegments = uiIndex >= 0 ? segments.slice(0, uiIndex) : segments
      return stripLastGitSuffix(repoSegments)
    }
    case "bitbucket_data_center": {
      const projectsIndex = segments.findIndex(
        (segment) => segment.toLowerCase() === "projects"
      )
      if (
        projectsIndex >= 0 &&
        segments[projectsIndex + 2]?.toLowerCase() === "repos"
      ) {
        return stripLastGitSuffix([
          segments[projectsIndex + 1],
          segments[projectsIndex + 3],
        ])
      }
      const scmIndex = segments.findIndex(
        (segment) => segment.toLowerCase() === "scm"
      )
      if (scmIndex >= 0) {
        return stripLastGitSuffix(segments.slice(scmIndex + 1, scmIndex + 3))
      }
      return stripLastGitSuffix(segments.slice(0, 2))
    }
    case "github":
    case "bitbucket":
      return stripLastGitSuffix(segments.slice(0, 2))
  }
}

function validateRawRemote(
  raw: RawRemote,
  target: GitRemoteTarget
): GitRemoteParseResult {
  const label = GIT_PROVIDER_LABELS[target.provider]
  if (!raw.host) {
    return {
      kind: "invalid",
      message: `Paste the full repository URL from ${label}.`,
    }
  }
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
  switch (target.provider) {
    case "github":
      return "Enter owner/repository or a GitHub repository URL."
    case "gitlab":
      return "Enter group/project or a GitLab repository URL."
    case "bitbucket":
      return "Enter workspace/repository or a bitbucket.org repository URL."
    case "bitbucket_data_center":
      return "Enter PROJECT/repository or a Bitbucket Data Center repository URL."
  }
}

/** GitLab also accepts SSH aliases on subdomains, e.g. altssh.gitlab.com. */
function hostMatchesTarget(host: string, target: GitRemoteTarget): boolean {
  if (host === target.host) {
    return true
  }
  return target.provider === "gitlab" && host.endsWith(`.${target.host}`)
}

function normalizeHost(host: string): string {
  return host.toLowerCase().replace(/^www\./, "")
}

function stripGitSuffix(path: string): string {
  return path.replace(/\/+$/, "").replace(/\.git$/, "")
}

function stripLastGitSuffix(segments: string[]): string[] {
  const cleaned = segments.filter(Boolean)
  const last = cleaned.at(-1)
  if (last === undefined) {
    return cleaned
  }
  return [...cleaned.slice(0, -1), last.replace(/\.git$/, "")]
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
