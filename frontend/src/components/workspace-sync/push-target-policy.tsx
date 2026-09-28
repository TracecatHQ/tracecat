"use client"

import { GitBranchIcon, GitPullRequestIcon } from "lucide-react"
import type { VcsProvider } from "@/client"
import { ToggleTabs } from "@/components/ui/toggle-tabs"
import { cn } from "@/lib/utils"

export type WorkspaceSyncPushMode = "pull-request" | "direct"

interface WorkspaceSyncPushOutcomeOptions {
  mode: WorkspaceSyncPushMode
  targetBranch: string
  defaultBranch: string | undefined
  isCreatingBranch: boolean
}

export interface WorkspaceSyncPushOutcome {
  createPr: boolean
  isPullRequestBlocked: boolean
  targetIsDefault: boolean
  willCreatePr: boolean
}

interface WorkspaceSyncPushButtonLabelOptions {
  outcome: WorkspaceSyncPushOutcome
  isCreatingBranch: boolean
  isPending: boolean
  provider: VcsProvider
}

interface WorkspaceSyncPushMessageOptions {
  outcome: WorkspaceSyncPushOutcome
  defaultBranch: string | undefined
  allowDirectPush?: boolean
  provider: VcsProvider
}

interface WorkspaceSyncPushModeTabsProps {
  value: WorkspaceSyncPushMode
  onValueChange: (value: WorkspaceSyncPushMode) => void
  provider: VcsProvider
  size?: "sm" | "md" | "lg"
}

export function buildRandomSyncBranchName(prefix: string): string {
  return `${prefix.replace(/\/$/, "")}-${randomBranchToken()}`
}

export function getWorkspaceSyncPushOutcome({
  mode,
  targetBranch,
  defaultBranch,
}: WorkspaceSyncPushOutcomeOptions): WorkspaceSyncPushOutcome {
  const normalizedTargetBranch = targetBranch.trim()
  const targetIsDefault =
    Boolean(defaultBranch) && normalizedTargetBranch === defaultBranch
  const isPullRequestBlocked = mode === "pull-request" && targetIsDefault

  return {
    createPr: mode === "pull-request",
    isPullRequestBlocked,
    targetIsDefault,
    willCreatePr: mode === "pull-request" && !isPullRequestBlocked,
  }
}

export function getWorkspaceSyncPushButtonLabel({
  outcome,
  isCreatingBranch,
  isPending,
  provider,
}: WorkspaceSyncPushButtonLabelOptions): string {
  const reviewRequestAbbreviation = getReviewRequestAbbreviation(provider)
  if (isPending) {
    return "Pushing..."
  }
  if (!outcome.createPr) {
    return "Push directly"
  }
  if (isCreatingBranch) {
    return `Push & open ${reviewRequestAbbreviation}`
  }
  return `Update branch & open ${reviewRequestAbbreviation}`
}

export function getWorkspaceSyncPushResultLabel({
  outcome,
  defaultBranch,
  provider,
}: WorkspaceSyncPushMessageOptions): string {
  if (outcome.willCreatePr) {
    return `${getReviewRequestAbbreviation(provider)} into ${defaultBranch ?? "default branch"}`
  }
  if (outcome.isPullRequestBlocked) {
    return "Choose a non-default branch"
  }
  return "Direct commit"
}

export function getWorkspaceSyncPushWarning({
  outcome,
  defaultBranch,
  allowDirectPush = true,
  provider,
}: WorkspaceSyncPushMessageOptions): string | null {
  const reviewRequestLabel = getReviewRequestLabel(provider)
  if (outcome.isPullRequestBlocked) {
    if (!allowDirectPush) {
      return `Select or create a non-default branch to open a ${reviewRequestLabel} into ${defaultBranch ?? "the default branch"}.`
    }
    return `Select or create a non-default branch to open a ${reviewRequestLabel} into ${defaultBranch ?? "the default branch"}, or switch to direct push.`
  }
  if (!outcome.createPr && outcome.targetIsDefault) {
    return `This commits directly to ${defaultBranch ?? "the default branch"}. No ${reviewRequestLabel} will be created.`
  }
  if (!outcome.createPr) {
    return `This commits directly to the selected branch. No ${reviewRequestLabel} will be created.`
  }
  return null
}

export function getReviewRequestAbbreviation(provider: VcsProvider): string {
  return provider === "gitlab" ? "MR" : "PR"
}

export function getReviewRequestLabel(provider: VcsProvider): string {
  return provider === "gitlab" ? "merge request" : "pull request"
}

/**
 * Severity-styled advisory for the push composer. Renders nothing when there is
 * no warning to show.
 */
export function WorkspaceSyncPushWarning({
  warning,
  blocked,
  className,
}: {
  warning: string | null | undefined
  blocked: boolean
  className?: string
}) {
  if (!warning) {
    return null
  }
  return (
    <p
      className={cn(
        "text-[11px]",
        blocked ? "text-destructive" : "text-amber-700",
        className
      )}
    >
      {warning}
    </p>
  )
}

export function WorkspaceSyncPushModeTabs({
  value,
  onValueChange,
  provider,
  size = "sm",
}: WorkspaceSyncPushModeTabsProps) {
  const reviewRequestAbbreviation = getReviewRequestAbbreviation(provider)
  return (
    <ToggleTabs<WorkspaceSyncPushMode>
      value={value}
      onValueChange={onValueChange}
      size={size}
      showTooltips={false}
      options={[
        {
          value: "pull-request",
          content: (
            <span className="flex items-center gap-1.5">
              <GitPullRequestIcon className="size-3.5" />
              Open {reviewRequestAbbreviation}
            </span>
          ),
        },
        {
          value: "direct",
          content: (
            <span className="flex items-center gap-1.5">
              <GitBranchIcon className="size-3.5" />
              Push directly
            </span>
          ),
        },
      ]}
    />
  )
}

function randomBranchToken(): string {
  const crypto = globalThis.crypto
  if (crypto?.getRandomValues) {
    const bytes = new Uint8Array(4)
    crypto.getRandomValues(bytes)
    return Array.from(bytes, (byte) => byte.toString(16).padStart(2, "0")).join(
      ""
    )
  }
  return Math.random().toString(36).slice(2, 10)
}
