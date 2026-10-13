"use client"

import { format } from "date-fns"
import {
  BotIcon,
  ChevronRightIcon,
  DownloadIcon,
  EyeIcon,
  InboxIcon,
  type LucideIcon,
} from "lucide-react"
import Link from "next/link"
import { useMemo, useState } from "react"
import type {
  FormActorRead,
  FormFieldBlock,
  FormPreview,
  FormRead,
  FormSubmissionRead,
} from "@/client"
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
import { toast } from "@/components/ui/use-toast"
import UserAvatar from "@/components/user-avatar"
import { useFormSubmissions } from "@/hooks/use-forms"
import { executionId } from "@/lib/event-history"
import { downloadFormFile, getFileAnswers } from "@/lib/forms/files"
import { capitalizeFirst, cn, formatFileSize, shortTimeAgo } from "@/lib/utils"

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

function isRecord(value: unknown): value is Record<string, unknown> {
  return typeof value === "object" && value !== null && !Array.isArray(value)
}

/** A `user` answer as the server expands it: "Name (email)". */
function formatUserAnswer(value: Record<string, unknown>): string | null {
  if (typeof value.user_id !== "string" || typeof value.email !== "string") {
    return null
  }
  const name = [value.first_name, value.last_name]
    .filter((part) => typeof part === "string" && part)
    .join(" ")
  return name ? `${name} (${value.email})` : value.email
}

/** A `file` answer as the server expands it: "name (size)". */
function formatFileAnswer(value: Record<string, unknown>): string | null {
  if (
    typeof value.id !== "string" ||
    typeof value.name !== "string" ||
    typeof value.size !== "number"
  ) {
    return null
  }
  return `${value.name} (${formatFileSize(value.size)})`
}

/**
 * Show an answer as text: scalars as they are, people as name and email,
 * files as name and size, lists one item per line, anything else as JSON.
 */
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
  if (Array.isArray(value)) {
    return value.length === 0 ? "-" : value.map(formatAnswer).join("\n")
  }
  if (isRecord(value)) {
    // An unanswered `user` or `file` field keeps its `{}` default.
    if (Object.keys(value).length === 0) {
      return "-"
    }
    const text = formatUserAnswer(value) ?? formatFileAnswer(value)
    if (text) {
      return text
    }
  }
  return JSON.stringify(value, null, 2)
}

/**
 * Map each input name to the label its field shows on the form.
 *
 * Hidden and autofill inputs have no field in a preview, so they are absent
 * and the caller falls back to the input name.
 */
export function getFieldLabels(
  preview: Pick<FormPreview, "blocks"> | null | undefined
): Record<string, string> {
  const labels: Record<string, string> = {}
  for (const block of preview?.blocks ?? []) {
    if (block.type === "field") {
      labels[block.name] = block.label
    }
  }
  return labels
}

/**
 * Put a submission's answers in the order the form shows its fields.
 *
 * Answers are stored as JSONB, which does not keep key order. Inputs with no
 * field on the form (hidden and autofill inputs) follow, in stored order.
 */
export function orderAnswers(
  answers: Record<string, unknown>,
  labels: Record<string, string>
): [string, unknown][] {
  const shown = Object.keys(labels).filter((name) => name in answers)
  const rest = Object.keys(answers).filter((name) => !(name in labels))
  return [...shown, ...rest].map((name) => [name, answers[name]])
}

/** Map each input name to the field the form shows for it. */
export function getFieldBlocks(
  preview: Pick<FormPreview, "blocks"> | null | undefined
): Record<string, FormFieldBlock> {
  const fields: Record<string, FormFieldBlock> = {}
  for (const block of preview?.blocks ?? []) {
    if (block.type === "field") {
      fields[block.name] = block
    }
  }
  return fields
}

/** A stored date or datetime as the requester picked it, or `null`. */
function formatDateAnswer(value: string, widget: string): string | null {
  if (widget === "date") {
    // A `date` field on a `datetime` input is stored at midnight. Read the
    // day from the text so no time zone moves it.
    const day = /^(\d{4})-(\d{2})-(\d{2})/.exec(value)
    return day
      ? format(
          new Date(Number(day[1]), Number(day[2]) - 1, Number(day[3])),
          "MMM d, yyyy"
        )
      : null
  }
  const time = new Date(value)
  return Number.isNaN(time.getTime()) ? null : format(time, "MMM d, yyyy p")
}

/**
 * Show an answer the way its field showed it: an option by its label, a
 * date as a date. Anything else falls back to {@link formatAnswer}.
 */
export function formatFieldAnswer(
  value: unknown,
  field: FormFieldBlock | undefined
): string {
  if (!field) {
    return formatAnswer(value)
  }
  if (Array.isArray(value) && value.length > 0) {
    return value.map((item) => formatFieldAnswer(item, field)).join("\n")
  }
  if (typeof value === "string" && value !== "") {
    const option = field.options?.find((item) => item.value === value)
    if (option) {
      return option.label
    }
    if (field.widget === "date" || field.widget === "datetime") {
      return formatDateAnswer(value, field.widget) ?? value
    }
  }
  return formatAnswer(value)
}

/**
 * One answer of a submission. Files are buttons that download them; anything
 * else is text.
 */
function Answer({
  value,
  field,
  workspaceId,
  formId,
}: {
  value: unknown
  /** The field the answer was given in, if the form shows one. */
  field?: FormFieldBlock
  workspaceId: string
  formId: string
}) {
  const files = getFileAnswers(value)
  if (!files) {
    return formatFieldAnswer(value, field)
  }

  async function download(fileId: string) {
    try {
      await downloadFormFile({ workspaceId, formId, fileId })
    } catch {
      toast({
        title: "Could not download the file",
        description: "It may have been deleted. Reload and try again.",
        variant: "destructive",
      })
    }
  }

  return (
    <ul className="flex flex-col items-start gap-1">
      {files.map((file) => (
        <li key={file.id} className="max-w-full">
          <button
            type="button"
            onClick={() => download(file.id)}
            aria-label={`Download ${file.name}`}
            className="flex max-w-full items-center gap-1.5 rounded-sm text-left underline-offset-2 hover:underline focus-visible:outline-none focus-visible:ring-1 focus-visible:ring-inset focus-visible:ring-ring"
          >
            <DownloadIcon className="size-3 shrink-0 text-muted-foreground" />
            <span className="truncate">{file.name}</span>
            <span className="shrink-0 text-muted-foreground">
              {formatFileSize(file.size)}
            </span>
          </button>
        </li>
      ))}
    </ul>
  )
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
  labels,
  fields,
}: {
  submission: FormSubmissionRead
  workspaceId: string
  labels: Record<string, string>
  fields: Record<string, FormFieldBlock>
}) {
  const [open, setOpen] = useState(false)
  const actor = submission.submitted_by
  const name = actorName(actor)
  const answers = orderAnswers(submission.answers, labels)
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
              <dt
                title={key}
                className={cn(
                  "truncate text-[11px] text-muted-foreground",
                  // An input with no field on the form shows its raw name.
                  !labels[key] && "font-mono"
                )}
              >
                {labels[key] ?? key}
              </dt>
              <dd className="mt-0.5 whitespace-pre-wrap break-words">
                <Answer
                  value={value}
                  field={fields[key]}
                  workspaceId={workspaceId}
                  formId={submission.form_id}
                />
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
  preview,
}: {
  form: FormRead
  /** Whether the caller holds `form:submission:read`. */
  canReadSubmissions: boolean
  /** The form's preview, for the labels answers are shown under. */
  preview?: FormPreview | null
}) {
  const labels = useMemo(() => getFieldLabels(preview), [preview])
  const fields = useMemo(() => getFieldBlocks(preview), [preview])
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
              labels={labels}
              fields={fields}
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
