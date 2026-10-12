"use client"

import { format } from "date-fns"
import {
  BotIcon,
  ChevronRightIcon,
  EyeIcon,
  InboxIcon,
  type LucideIcon,
} from "lucide-react"
import Link from "next/link"
import { useState } from "react"
import type { FormActorRead, FormRead, FormSubmissionRead } from "@/client"
import { getExecutionStatusIcon } from "@/components/executions/nav"
import { CenteredSpinner } from "@/components/loading/spinner"
import { Button } from "@/components/ui/button"
import {
  Empty,
  EmptyDescription,
  EmptyHeader,
  EmptyMedia,
  EmptyTitle,
} from "@/components/ui/empty"
import {
  Tooltip,
  TooltipContent,
  TooltipTrigger,
} from "@/components/ui/tooltip"
import UserAvatar from "@/components/user-avatar"
import { useFormSubmissions } from "@/hooks/use-forms"
import { executionId } from "@/lib/event-history"
import { capitalizeFirst, cn, shortTimeAgo } from "@/lib/utils"

function actorName(actor: FormActorRead | null | undefined): string {
  if (!actor) {
    return "Unknown"
  }
  if (actor.type === "service_account") {
    return actor.name ?? "Service account"
  }
  const fullName = [actor.first_name, actor.last_name].filter(Boolean).join(" ")
  return fullName || actor.email || "Unknown"
}

/** Show an answer as text: scalars as they are, anything else as JSON. */
export function formatAnswer(value: unknown): string {
  if (value === null || value === undefined || value === "") {
    return "-"
  }
  if (typeof value === "boolean") {
    return value ? "Yes" : "No"
  }
  if (typeof value === "string" || typeof value === "number") {
    return String(value)
  }
  return JSON.stringify(value, null, 2)
}

function StatTile({
  icon: Icon,
  label,
  value,
}: {
  icon: LucideIcon
  label: string
  value: number
}) {
  return (
    <div className="flex-1 rounded-md border px-3 py-2">
      <dt className="flex items-center gap-1.5 text-xs text-muted-foreground">
        <Icon className="size-3.5" />
        {label}
      </dt>
      <dd className="mt-1 text-xl font-semibold tabular-nums">
        {value.toLocaleString()}
      </dd>
    </div>
  )
}

function runHref(workspaceId: string, wfExecId: string): string | null {
  try {
    const { wf, exec } = executionId(wfExecId)
    return `/workspaces/${workspaceId}/workflows/${encodeURIComponent(wf)}/executions/${encodeURIComponent(exec)}`
  } catch {
    return null
  }
}

function SubmissionRow({
  submission,
  workspaceId,
}: {
  submission: FormSubmissionRead
  workspaceId: string
}) {
  const [open, setOpen] = useState(false)
  const actor = submission.submitted_by
  const name = actorName(actor)
  const answers = Object.entries(submission.answers)
  const href = submission.wf_exec_id
    ? runHref(workspaceId, submission.wf_exec_id)
    : null
  const submittedAt = new Date(submission.submitted_at)
  const answersId = `answers-${submission.id}`

  return (
    <li className="border-b border-border/50">
      <div className="flex items-center gap-2 px-3 py-1.5 text-xs">
        <button
          type="button"
          className="flex size-6 flex-none items-center justify-center rounded-sm hover:bg-muted focus-visible:outline-none focus-visible:ring-1 focus-visible:ring-inset focus-visible:ring-ring"
          aria-expanded={open}
          aria-controls={answersId}
          aria-label={`${open ? "Hide" : "Show"} answers of ${submission.reference}`}
          onClick={() => setOpen((value) => !value)}
        >
          <ChevronRightIcon
            className={cn(
              "size-3.5 text-muted-foreground transition-transform",
              open && "rotate-90"
            )}
          />
        </button>
        {actor?.type === "user" && actor.email ? (
          <UserAvatar
            email={actor.email}
            firstName={actor.first_name}
            alt={name}
            className="size-5 flex-none"
            fallbackClassName="text-[9px]"
          />
        ) : (
          <span className="flex size-5 flex-none items-center justify-center rounded-full bg-muted">
            <BotIcon className="size-3 text-muted-foreground" />
          </span>
        )}
        <span className="truncate font-medium">{name}</span>
        {actor?.email && actor.email !== name && (
          <span className="hidden min-w-0 truncate text-muted-foreground lg:inline">
            {actor.email}
          </span>
        )}
        <span className="flex-1" />
        <span className="flex-none font-mono text-[11px] text-muted-foreground">
          {submission.reference}
        </span>
        {submission.run_status && (
          <span className="flex flex-none items-center gap-1 text-muted-foreground">
            {getExecutionStatusIcon(submission.run_status, "size-3.5")}
            <span className="hidden xl:inline">
              {capitalizeFirst(
                submission.run_status.toLowerCase().replace(/_/g, " ")
              )}
            </span>
          </span>
        )}
        {href && (
          <Link
            href={href}
            className="flex-none font-medium text-primary hover:underline"
          >
            Run
          </Link>
        )}
        <Tooltip>
          <TooltipTrigger asChild>
            <time
              dateTime={submission.submitted_at}
              className="w-14 flex-none text-right text-muted-foreground"
            >
              {shortTimeAgo(submittedAt)}
            </time>
          </TooltipTrigger>
          <TooltipContent>{format(submittedAt, "PPpp")}</TooltipContent>
        </Tooltip>
      </div>
      {open && (
        <dl
          id={answersId}
          className="grid grid-cols-1 gap-x-6 gap-y-3 bg-muted/30 px-11 py-3 text-xs sm:grid-cols-2"
        >
          {answers.length === 0 && (
            <div className="text-muted-foreground">No answers.</div>
          )}
          {answers.map(([key, value]) => (
            <div key={key} className="min-w-0">
              <dt className="truncate font-mono text-[11px] text-muted-foreground">
                {key}
              </dt>
              <dd className="mt-0.5 whitespace-pre-wrap break-words">
                {formatAnswer(value)}
              </dd>
            </div>
          ))}
        </dl>
      )}
    </li>
  )
}

/**
 * The Activity tab of a form: how often it was viewed and submitted, and
 * every submission with its answers.
 *
 * Builder-only. The run status and the link to the run appear when the
 * server sends them, which it does for callers with `form:submission:read`.
 */
export function FormActivityTab({
  form,
  canReadSubmissions,
}: {
  form: FormRead
  /** Whether the caller holds `form:submission:read`. */
  canReadSubmissions: boolean
}) {
  const {
    submissions,
    submissionsIsLoading,
    submissionsError,
    hasNextPage,
    isFetchingNextPage,
    fetchNextPage,
    refetch,
  } = useFormSubmissions(
    { workspaceId: form.workspace_id, formId: form.id },
    { enabled: canReadSubmissions }
  )

  if (!canReadSubmissions) {
    return (
      <Empty className="py-12">
        <EmptyHeader>
          <EmptyTitle>No access to submissions</EmptyTitle>
          <EmptyDescription>
            Your role can edit this form but can't read what people submit.
          </EmptyDescription>
        </EmptyHeader>
      </Empty>
    )
  }

  let list = null
  if (submissionsIsLoading) {
    list = <CenteredSpinner />
  } else if (submissionsError) {
    list = (
      <div className="flex items-center justify-between gap-3 px-3 py-4 text-xs">
        <span className="text-destructive">Couldn't load submissions.</span>
        <Button
          type="button"
          variant="outline"
          size="sm"
          className="h-6 px-2.5 text-xs"
          onClick={() => refetch()}
        >
          Retry
        </Button>
      </div>
    )
  } else if (submissions.length === 0) {
    list = (
      <Empty className="py-12">
        <EmptyHeader>
          <EmptyMedia variant="icon">
            <InboxIcon className="size-5 text-muted-foreground/60" />
          </EmptyMedia>
          <EmptyTitle>No submissions yet</EmptyTitle>
          <EmptyDescription>
            Submissions show up here once someone fills in the form.
          </EmptyDescription>
        </EmptyHeader>
      </Empty>
    )
  } else {
    list = (
      <>
        <ul>
          {submissions.map((submission) => (
            <SubmissionRow
              key={submission.id}
              submission={submission}
              workspaceId={form.workspace_id}
            />
          ))}
        </ul>
        {hasNextPage && (
          <div className="flex justify-center py-3">
            <Button
              type="button"
              variant="outline"
              size="sm"
              className="h-7 text-xs"
              disabled={isFetchingNextPage}
              onClick={() => fetchNextPage()}
            >
              {isFetchingNextPage ? "Loading..." : "Load more"}
            </Button>
          </div>
        )}
      </>
    )
  }

  return (
    <div className="h-full overflow-y-auto">
      {form.counts && (
        <dl className="flex gap-3 p-3">
          <StatTile icon={EyeIcon} label="Views" value={form.counts.views} />
          <StatTile
            icon={InboxIcon}
            label="Submissions"
            value={form.counts.submissions}
          />
        </dl>
      )}
      <div className="flex h-8 items-center border-y px-3 text-xs">
        <span className="font-medium">Submissions</span>
        <span className="flex-1" />
        <span className="text-muted-foreground">Newest first</span>
      </div>
      {list}
    </div>
  )
}
