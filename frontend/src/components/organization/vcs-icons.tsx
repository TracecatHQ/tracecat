import { GithubIcon, GitlabIcon } from "lucide-react"
import type { SVGProps } from "react"
import { GitHubIcon } from "@/components/icons"
import { cn } from "@/lib/utils"

/** Bitbucket brand mark, from Simple Icons (CC0). */
export function BitbucketIcon(props: SVGProps<SVGSVGElement>) {
  return (
    <svg viewBox="0 0 24 24" fill="currentColor" aria-hidden="true" {...props}>
      <path d="M.778 1.213a.768.768 0 00-.768.892l3.263 19.81c.084.5.515.868 1.022.873H19.95a.772.772 0 00.77-.646l3.27-20.03a.768.768 0 00-.768-.891zM14.52 15.53H9.522L8.17 8.466h7.561z" />
    </svg>
  )
}

/** GitLab brand mark, from Simple Icons (CC0). */
export function GitLabIcon(props: SVGProps<SVGSVGElement>) {
  return (
    <svg viewBox="0 0 24 24" fill="currentColor" aria-hidden="true" {...props}>
      <path d="m23.6004 9.5927-.0337-.0862L20.3.9814a.851.851 0 0 0-.3362-.405.8748.8748 0 0 0-.9997.0539.8748.8748 0 0 0-.29.4399l-2.2055 6.748H7.5375l-2.2057-6.748a.8573.8573 0 0 0-.29-.4412.8748.8748 0 0 0-.9997-.0537.8585.8585 0 0 0-.3362.4049L.4332 9.5015l-.0325.0862a6.0657 6.0657 0 0 0 2.0119 7.0105l.0113.0087.03.0213 4.976 3.7264 2.462 1.8633 1.4995 1.1321a1.0085 1.0085 0 0 0 1.2197 0l1.4995-1.1321 2.4619-1.8633 5.006-3.7489.0125-.01a6.0682 6.0682 0 0 0 2.0094-7.003z" />
    </svg>
  )
}

/** Organization-level Git providers with stored credentials. */
export type VcsProviderId =
  | "github"
  | "gitlab"
  | "bitbucket"
  | "bitbucket_data_center"

/** Provider logo in its brand color. */
export function VcsProviderLogo({
  provider,
  className,
}: {
  provider: VcsProviderId
  className?: string
}) {
  switch (provider) {
    case "github":
      return (
        <GitHubIcon
          aria-hidden="true"
          className={cn("shrink-0 text-foreground", className)}
        />
      )
    case "gitlab":
      return <GitLabIcon className={cn("shrink-0 text-[#FC6D26]", className)} />
    case "bitbucket":
    case "bitbucket_data_center":
      return (
        <BitbucketIcon className={cn("shrink-0 text-[#0052CC]", className)} />
      )
  }
}

/** List-row mark: brand logo in light mode, the muted outline mark in dark mode. */
export function VcsProviderIcon({
  provider,
  size = "default",
}: {
  provider: VcsProviderId
  /** `sm` fits a size-9 tile. */
  size?: "default" | "sm"
}) {
  const isSmall = size === "sm"
  return (
    <span
      className={cn(
        "flex shrink-0 items-center justify-center",
        isSmall ? "size-5" : "size-6"
      )}
    >
      <VcsProviderLogo
        provider={provider}
        className={cn("dark:hidden", isSmall ? "size-5" : "size-6")}
      />
      <MutedVcsProviderIcon
        provider={provider}
        className={cn(
          "hidden text-muted-foreground dark:block",
          isSmall ? "size-4" : "size-5"
        )}
      />
    </span>
  )
}

function MutedVcsProviderIcon({
  provider,
  className,
}: {
  provider: VcsProviderId
  className: string
}) {
  switch (provider) {
    case "github":
      return <GithubIcon aria-hidden="true" className={className} />
    case "gitlab":
      return <GitlabIcon aria-hidden="true" className={className} />
    case "bitbucket":
    case "bitbucket_data_center":
      return <BitbucketIcon className={className} />
  }
}
