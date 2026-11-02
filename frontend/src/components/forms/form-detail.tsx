"use client"

import { format } from "date-fns"
import {
  BotIcon,
  ChevronRightIcon,
  DownloadIcon,
  EyeIcon,
  InboxIcon,
  SearchXIcon,
  TriangleAlertIcon,
} from "lucide-react"
import Link from "next/link"
import { useSearchParams } from "next/navigation"
import { useMemo, useState } from "react"
import type {
  FormActorRead,
  FormFieldBlock,
  FormPreview,
  FormRead,
  FormSubmissionRead,
} from "@/client"
import { useScopeCheck } from "@/components/auth/scope-guard"
import { getExecutionStatusIcon } from "@/components/executions/nav"
import { FormConfigTab } from "@/components/forms/form-config-tab"
import { FormRenderer } from "@/components/forms/form-renderer"
import { formViewFromParam } from "@/components/forms/form-view-toggle"
import { CenteredSpinner } from "@/components/loading/spinner"
import { AlertNotification } from "@/components/notifications"
import { Badge } from "@/components/ui/badge"
import { Button } from "@/components/ui/button"
import {
  Empty,
  EmptyContent,
  EmptyDescription,
  EmptyHeader,
  EmptyMedia,
  EmptyTitle,
} from "@/components/ui/empty"
import {
  ResizableHandle,
  ResizablePanel,
  ResizablePanelGroup,
} from "@/components/ui/resizable"
import { Tabs, TabsContent } from "@/components/ui/tabs"
import {
  Tooltip,
  TooltipContent,
  TooltipTrigger,
} from "@/components/ui/tooltip"
import { toast } from "@/components/ui/use-toast"
import UserAvatar from "@/components/user-avatar"
import {
  useFormConfigCheck,
  useFormConfigDraft,
  useFormConfigScaffold,
} from "@/hooks/use-form-config"
import { useFormDetail, useFormSubmissions } from "@/hooks/use-forms"
import { executionId } from "@/lib/event-history"
import { getFormLoadFailure } from "@/lib/forms/errors"
import { downloadFormFile, getFileAnswers } from "@/lib/forms/files"
import { capitalizeFirst, cn, formatFileSize, shortTimeAgo } from "@/lib/utils"
import { useWorkspaceId } from "@/providers/workspace-id"

function actorName(actor: FormActorRead | null | undefined): string {
  if (actor?.type === "service_account") {
    return actor.name ?? "Service account"
  }
  const fullName = [actor?.first_name, actor?.last_name]
    .filter(Boolean)
    .join(" ")
  return fullName || actor?.email || "Unknown"
}

/**
 * Show an answer as text, the way its field showed it: an option by its
 * label, a date as a date, a person as "Name (email)", lists one item per
 * line, anything else as JSON.
 */
function formatAnswer(value: unknown, field?: FormFieldBlock): string {
  // An unanswered `user` or `file` field keeps its `{}` default.
  if (
    value === null ||
    value === undefined ||
    value === "" ||
    (typeof value === "object" && Object.keys(value).length === 0)
  ) {
    return "-"
  }
  if (Array.isArray(value)) {
    return value.map((item) => formatAnswer(item, field)).join("\n")
  }
  if (typeof value === "boolean") {
    return value ? "Yes" : "No"
  }
  if (typeof value === "string") {
    const option = field?.options?.find((item) => item.value === value)
    // A `date` field on a `datetime` input is stored at midnight. Read the
    // day from the text so no time zone moves it.
    const day =
      field?.widget === "date" && /^(\d{4})-(\d{2})-(\d{2})/.exec(value)
    const time = new Date(value)
    if (option) {
      return option.label
    }
    if (day) {
      return format(new Date(+day[1], +day[2] - 1, +day[3]), "MMM d, yyyy")
    }
    if (field?.widget === "datetime" && !Number.isNaN(time.getTime())) {
      return format(time, "MMM d, yyyy p")
    }
    return value
  }
  if (typeof value === "number") {
    return String(value)
  }
  const record = value as Record<string, unknown>
  if (typeof record.user_id === "string" && typeof record.email === "string") {
    const name = [record.first_name, record.last_name]
      .filter((part) => typeof part === "string" && part)
      .join(" ")
    return name ? `${name} (${record.email})` : record.email
  }
  return JSON.stringify(value, null, 2)
}

/** One answer of a submission. Files are buttons that download them. */
function Answer({
  value,
  field,
  workspaceId,
  formId,
}: {
  value: unknown
  field?: FormFieldBlock
  workspaceId: string
  formId: string
}) {
  const files = getFileAnswers(value)
  if (!files) {
    return formatAnswer(value, field)
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
  fields,
  canOpenRun,
}: {
  submission: FormSubmissionRead
  workspaceId: string
  /** The form's fields by input name, in the order the form shows them. */
  fields: Record<string, FormFieldBlock>
  /** Whether the caller holds `workflow:read`, which the run's page needs. */
  canOpenRun: boolean
}) {
  const [open, setOpen] = useState(false)
  const actor = submission.submitted_by
  const name = actorName(actor)
  // Answers are JSONB, which does not keep key order. Show them in the
  // form's field order; inputs with no field (hidden, autofill) follow.
  const answers = submission.answers
  const names = [
    ...Object.keys(fields).filter((key) => key in answers),
    ...Object.keys(answers).filter((key) => !(key in fields)),
  ]
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
        {href &&
          (canOpenRun ? (
            <Link
              href={href}
              className="flex-none font-medium text-primary hover:underline"
            >
              Run
            </Link>
          ) : (
            <span className="flex-none text-muted-foreground">Run</span>
          ))}
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
          {names.length === 0 && (
            <div className="text-muted-foreground">No answers.</div>
          )}
          {names.map((key) => (
            <div key={key} className="min-w-0">
              <dt
                title={key}
                className={cn(
                  "truncate text-[11px] text-muted-foreground",
                  // An input with no field on the form shows its raw name.
                  !fields[key] && "font-mono"
                )}
              >
                {fields[key]?.label ?? key}
              </dt>
              <dd className="mt-0.5 whitespace-pre-wrap break-words">
                <Answer
                  value={answers[key]}
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
 * The Activity tab of a form: view and submission counts, and every
 * submission with its answers, run status and run link.
 */
function FormActivityTab({
  form,
  canReadSubmissions,
  preview,
}: {
  form: FormRead
  /** Whether the caller holds `form:submission:read`. */
  canReadSubmissions: boolean
  /** The form's preview, for the labels answers are shown under. */
  preview: FormPreview | null
}) {
  const fields = useMemo(
    () =>
      Object.fromEntries(
        (preview?.blocks ?? []).flatMap((block) =>
          block.type === "field" ? [[block.name, block]] : []
        )
      ) as Record<string, FormFieldBlock>,
    [preview]
  )
  const submissions = useFormSubmissions(
    { workspaceId: form.workspace_id, formId: form.id },
    { enabled: canReadSubmissions }
  )
  const canOpenRuns = useScopeCheck("workflow:read") === true

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
  if (submissions.isLoading) {
    list = <CenteredSpinner />
  } else if (submissions.error) {
    list = (
      <div className="flex items-center justify-between gap-3 px-3 py-4 text-xs">
        <span className="text-destructive">Couldn't load submissions.</span>
        <Button
          variant="outline"
          className="h-6 px-2.5 text-xs"
          onClick={() => submissions.refetch()}
        >
          Retry
        </Button>
      </div>
    )
  } else if (submissions.items.length === 0) {
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
          {submissions.items.map((submission) => (
            <SubmissionRow
              key={submission.id}
              submission={submission}
              workspaceId={form.workspace_id}
              fields={fields}
              canOpenRun={canOpenRuns}
            />
          ))}
        </ul>
        {submissions.hasNextPage && (
          <div className="flex justify-center py-3">
            <Button
              variant="outline"
              className="h-7 text-xs"
              disabled={submissions.isFetchingNextPage}
              onClick={() => submissions.fetchNextPage()}
            >
              {submissions.isFetchingNextPage ? "Loading..." : "Load more"}
            </Button>
          </div>
        )}
      </>
    )
  }

  const counts = form.counts
  return (
    <div className="h-full overflow-y-auto">
      {counts && (
        <dl className="flex gap-3 p-3">
          {(
            [
              [EyeIcon, "Views", counts.views],
              [InboxIcon, "Submissions", counts.submissions],
            ] as const
          ).map(([Icon, label, value]) => (
            <div key={label} className="flex-1 rounded-md border px-3 py-2">
              <dt className="flex items-center gap-1.5 text-xs text-muted-foreground">
                <Icon className="size-3.5" />
                {label}
              </dt>
              <dd className="mt-1 text-xl font-semibold tabular-nums">
                {value.toLocaleString()}
              </dd>
            </div>
          ))}
        </dl>
      )}
      <div className="flex h-8 items-center border-y px-3 text-xs">
        <span className="flex-1 font-medium">Submissions</span>
        <span className="text-muted-foreground">Newest first</span>
      </div>
      {list}
    </div>
  )
}

function FormDetailBody({ form }: { form: FormRead }) {
  const searchParams = useSearchParams()
  const canEdit = useScopeCheck("form:update") === true
  const canReadSubmissions = useScopeCheck("form:submission:read") === true
  // Picked by the icon tabs in the page header.
  const tab = formViewFromParam(searchParams?.get("tab"))

  // The buffer lives above the tabs, so it survives a visit to Activity and
  // feeds the preview. A read-only builder's buffer is the saved config.
  const scaffold = useFormConfigScaffold(form)
  const draft = useFormConfigDraft(form, canEdit ? scaffold.text : "")
  const check = useFormConfigCheck(
    { workspaceId: form.workspace_id, formId: form.id },
    draft.text
  )
  // While the config has errors, the last good preview stays, dimmed, so
  // the author keeps their bearings while they type.
  const isStale = check.hasErrors || check.checkFailed

  return (
    <ResizablePanelGroup direction="horizontal" className="h-full">
      <ResizablePanel defaultSize={55} minSize={30}>
        <Tabs value={tab} className="flex h-full min-h-0 flex-col">
          <TabsContent value="config" className="mt-0 min-h-0 flex-1">
            <FormConfigTab
              form={form}
              canEdit={canEdit}
              draft={draft}
              check={check}
              fields={scaffold.fields}
            />
          </TabsContent>
          <TabsContent value="activity" className="mt-0 min-h-0 flex-1">
            <FormActivityTab
              form={form}
              canReadSubmissions={canReadSubmissions}
              preview={check.preview}
            />
          </TabsContent>
        </Tabs>
      </ResizablePanel>
      <ResizableHandle />
      <ResizablePanel defaultSize={45} minSize={25}>
        <section aria-label="Preview" className="flex h-full min-h-0 flex-col">
          <header className="flex h-9 flex-none items-center gap-2 border-b px-3 text-xs font-medium">
            <span className="flex-1">Preview</span>
            {check.preview && isStale && (
              <span className="truncate font-normal text-muted-foreground">
                {check.checkFailed
                  ? "Couldn't check the config"
                  : "Showing the last valid config"}
              </span>
            )}
            {draft.isDirty && (
              <Badge
                variant="secondary"
                className="h-5 flex-none px-2 text-[10px] font-normal"
              >
                Unsaved changes
              </Badge>
            )}
          </header>
          <div className="min-h-0 flex-1 overflow-y-auto bg-muted/30 p-4">
            {check.preview ? (
              <div
                className={cn("transition-opacity", isStale && "opacity-50")}
                data-stale={isStale}
              >
                {/* Submitting a preview validates and confirms, sending nothing. */}
                <FormRenderer
                  workspaceId={form.workspace_id}
                  formId={form.id}
                  form={check.preview}
                  mode="preview"
                  className="p-5 sm:p-5"
                />
              </div>
            ) : (
              isStale && (
                <Empty className="py-12">
                  <EmptyHeader>
                    <EmptyMedia variant="icon">
                      <TriangleAlertIcon className="size-5" />
                    </EmptyMedia>
                    <EmptyTitle>No preview yet</EmptyTitle>
                    <EmptyDescription>
                      {check.checkFailed
                        ? `The config could not be checked. ${canEdit ? "Edit it" : "Reload the page"} to try again.`
                        : "Fix the problems in the config to see the form."}
                    </EmptyDescription>
                  </EmptyHeader>
                </Empty>
              )
            )}
          </div>
        </section>
      </ResizablePanel>
    </ResizablePanelGroup>
  )
}

/**
 * The builder's page for one form: the config editor and Activity on the
 * left, a live preview on the right. A form the caller cannot open (404)
 * shows "Form not found".
 */
export function FormDetail({ formId }: { formId: string }) {
  const workspaceId = useWorkspaceId()
  const {
    data: form,
    isLoading: formIsLoading,
    error: formError,
  } = useFormDetail({
    workspaceId,
    formId,
  })

  if (formIsLoading) {
    return <CenteredSpinner />
  }
  if (formError && getFormLoadFailure(formError) !== "not_found") {
    return (
      <div className="p-6">
        <AlertNotification level="error" message="Error loading the form." />
      </div>
    )
  }
  if (!form) {
    return (
      <Empty className="h-full">
        <EmptyHeader>
          <EmptyMedia variant="icon">
            <SearchXIcon className="size-5" />
          </EmptyMedia>
          <EmptyTitle>Form not found</EmptyTitle>
          <EmptyDescription>
            It may have been deleted, or you may not have access to it.
          </EmptyDescription>
        </EmptyHeader>
        <EmptyContent>
          <Button asChild variant="outline" size="sm">
            <Link href={`/workspaces/${workspaceId}/forms`}>Back to forms</Link>
          </Button>
        </EmptyContent>
      </Empty>
    )
  }
  // A different form is a different buffer.
  return <FormDetailBody key={form.id} form={form} />
}
