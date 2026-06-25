"use client"

import { GitBranchIcon, GitPullRequestIcon } from "lucide-react"
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
}

interface WorkspaceSyncPushMessageOptions {
  outcome: WorkspaceSyncPushOutcome
  defaultBranch: string | undefined
}

interface WorkspaceSyncPushModeTabsProps {
  value: WorkspaceSyncPushMode
  onValueChange: (value: WorkspaceSyncPushMode) => void
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
}: WorkspaceSyncPushButtonLabelOptions): string {
  if (isPending) {
    return "Pushing..."
  }
  if (!outcome.createPr) {
    return "Push directly"
  }
  if (isCreatingBranch) {
    return "Push & open PR"
  }
  return "Update branch & open PR"
}

export function getWorkspaceSyncPushResultLabel({
  outcome,
  defaultBranch,
}: WorkspaceSyncPushMessageOptions): string {
  if (outcome.willCreatePr) {
    return `PR into ${defaultBranch ?? "default branch"}`
  }
  if (outcome.isPullRequestBlocked) {
    return "Choose a non-default branch"
  }
  return "Direct commit"
}

export function getWorkspaceSyncPushWarning({
  outcome,
  defaultBranch,
}: WorkspaceSyncPushMessageOptions): string | null {
  if (outcome.isPullRequestBlocked) {
    return `Select or create a non-default branch to open a pull request into ${defaultBranch ?? "the default branch"}, or switch to direct push.`
  }
  if (!outcome.createPr && outcome.targetIsDefault) {
    return `This commits directly to ${defaultBranch ?? "the default branch"}. No pull request will be created.`
  }
  if (!outcome.createPr) {
    return "This commits directly to the selected branch. No pull request will be created."
  }
  return null
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
  size = "sm",
}: WorkspaceSyncPushModeTabsProps) {
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
              Open PR
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
