"use client"

import { ArrowRightIcon } from "lucide-react"
import type {
  GitTagInfo,
  ManagedReleaseCandidateRead,
  ManagedTargetRead,
} from "@/client"

/** Props for {@link ReleaseSteps}. */
export interface ReleaseStepsProps {
  candidate: ManagedReleaseCandidateRead | undefined
  tags: GitTagInfo[]
  targets: ManagedTargetRead[]
}

/**
 * Shows the three separate steps that take a change to customers, and where
 * each one stands: what is published but not released, the latest release,
 * and how many targets run it.
 */
export function ReleaseSteps({ candidate, tags, targets }: ReleaseStepsProps) {
  const latest = tags[0]?.name
  const onLatest = latest
    ? targets.filter((target) => target.last_applied_tag === latest).length
    : 0
  let published = "Push from the source workspace's Git sync page."
  if (candidate) {
    const sha = candidate.commit_sha.substring(0, 7)
    const existingTags = candidate.existing_tags ?? []
    published =
      existingTags.length > 0
        ? `${candidate.branch} @ ${sha} is released as ${existingTags.join(", ")}.`
        : `${candidate.branch} @ ${sha} has unreleased changes.`
  }
  const steps = [
    { title: "1. Publish", detail: published },
    {
      title: "2. Release",
      detail: latest
        ? `Latest release is ${latest}.`
        : "No release yet. Cut one below.",
    },
    {
      title: "3. Deploy",
      detail: latest
        ? `${onLatest} of ${targets.length} targets run ${latest}.`
        : `${targets.length} targets assigned.`,
    },
  ]

  return (
    <ol className="grid gap-2 md:grid-cols-[1fr_auto_1fr_auto_1fr] md:items-stretch">
      {steps.map((step, index) => (
        <li key={step.title} className="contents">
          {index > 0 && (
            <ArrowRightIcon className="hidden size-4 self-center text-muted-foreground md:block" />
          )}
          <div className="space-y-1 rounded-md border px-3 py-2">
            <p className="text-sm font-medium">{step.title}</p>
            <p className="text-xs text-muted-foreground">{step.detail}</p>
          </div>
        </li>
      ))}
    </ol>
  )
}
