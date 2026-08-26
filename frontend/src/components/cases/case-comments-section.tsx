"use client"

import { zodResolver } from "@hookform/resolvers/zod"
import {
  AlertCircle,
  ArrowUpIcon,
  ChevronDown,
  ChevronUp,
  CircleCheckIcon,
  ClockIcon,
  LinkIcon,
  MoreHorizontal,
  PencilIcon,
  Trash2Icon,
  XCircleIcon,
} from "lucide-react"
import Link from "next/link"
import type React from "react"
import type { RefObject } from "react"
import { useCallback, useLayoutEffect, useRef, useState } from "react"
import { type UseFormReturn, useForm } from "react-hook-form"
import { z } from "zod"
import type { CaseCommentRead, CaseCommentThreadRead } from "@/client"
import {
  CaseCommentAgentAttribution,
  CaseCommentAgentInvocationList,
} from "@/components/cases/case-comment-agent"
import { CaseCommentViewer } from "@/components/cases/case-description-editor"
import {
  CaseEventTimestamp,
  CaseUserAvatar,
} from "@/components/cases/case-panel-common"
import { CommentMentionOverlay } from "@/components/cases/comment-mention-overlay"
import { CommentMentionPopover } from "@/components/cases/comment-mention-popover"
import {
  AlertDialog,
  AlertDialogAction,
  AlertDialogCancel,
  AlertDialogContent,
  AlertDialogDescription,
  AlertDialogFooter,
  AlertDialogHeader,
  AlertDialogTitle,
} from "@/components/ui/alert-dialog"
import { Badge } from "@/components/ui/badge"
import { Button } from "@/components/ui/button"
import {
  DropdownMenu,
  DropdownMenuContent,
  DropdownMenuItem,
  DropdownMenuTrigger,
} from "@/components/ui/dropdown-menu"
import {
  Form,
  FormControl,
  FormField,
  FormItem,
  FormMessage,
} from "@/components/ui/form"
import { Kbd } from "@/components/ui/kbd"
import { ScrollArea } from "@/components/ui/scroll-area"
import { Skeleton } from "@/components/ui/skeleton"
import { Textarea } from "@/components/ui/textarea"
import { toast } from "@/components/ui/use-toast"
import { useAuth } from "@/hooks/use-auth"
import { useCommentMentions } from "@/hooks/use-comment-mentions"
import { useEntitlements } from "@/hooks/use-entitlements"
import { SYSTEM_USER_READ, User } from "@/lib/auth"
import {
  createPastedImageFile,
  extractImageFiles,
  useCaseImageUpload,
} from "@/lib/cases/use-case-image-upload"
import type { TextSplice } from "@/lib/comment-mentions"
import { executionId, getWorkflowExecutionUrl } from "@/lib/event-history"
import {
  useCaseComments,
  useCaseCommentThreads,
  useCompactWorkflowExecution,
  useCreateCaseComment,
  useDeleteCaseComment,
  useUpdateCaseComment,
} from "@/lib/hooks"
import { cn, INSET_SURFACE } from "@/lib/utils"

/**
 * Validates the display text in the composer. The length limit lives in
 * `commentWireSchema` because it applies to the serialized value the API
 * receives, which can be longer than the display text.
 */
const commentFormSchema = z.object({
  content: z.string().min(1, { message: "Comment cannot be empty" }),
})

/** Validates the wire value sent to the API after mentions are serialized. */
const commentWireSchema = z
  .string()
  .max(25000, { message: "Comment cannot be longer than 25000 characters" })

/** Inline edits have no mentions, so display and wire text are the same. */
const commentEditFormSchema = z.object({
  content: commentFormSchema.shape.content.pipe(commentWireSchema),
})

type CommentFormSchema = z.infer<typeof commentFormSchema>

function getCommentUser(comment: CaseCommentRead) {
  return new User(comment.user ?? SYSTEM_USER_READ)
}

type WorkflowCommentStatus = "running" | "succeeded" | "failed"

function getWorkflowCommentStatus(
  comment: CaseCommentRead
): WorkflowCommentStatus {
  if (!comment.workflow) {
    return "running"
  }
  return comment.workflow.status
}

function getWorkflowStatusBadge(status: WorkflowCommentStatus) {
  switch (status) {
    case "succeeded":
      return (
        <span
          aria-label="Completed"
          className="inline-flex items-center"
          role="img"
        >
          <CircleCheckIcon className="size-4 fill-emerald-500 stroke-background" />
          <span className="sr-only">Completed</span>
        </span>
      )
    case "failed":
      return (
        <span
          aria-label="Error"
          className="inline-flex items-center"
          role="img"
        >
          <XCircleIcon className="size-4 fill-rose-500 stroke-background" />
          <span className="sr-only">Error</span>
        </span>
      )
    default:
      return (
        <span
          aria-label="In progress"
          className="inline-flex items-center"
          role="img"
        >
          <ClockIcon className="size-3.5 animate-pulse text-amber-500" />
          <span className="sr-only">In progress</span>
        </span>
      )
  }
}

function getWorkflowRunPath(
  workspaceId: string,
  wfExecId: string | null | undefined
): string | null {
  if (!wfExecId) {
    return null
  }
  try {
    const { wf, exec } = executionId(wfExecId)
    return getWorkflowExecutionUrl("", workspaceId, wf, exec)
  } catch {
    return null
  }
}

export function CommentSection({
  caseId,
  workspaceId,
}: {
  caseId: string
  workspaceId: string
}) {
  const { user: currentUser } = useAuth()
  const { hasEntitlement, isLoading: entitlementsLoading } = useEntitlements()
  const repliesEnabled = hasEntitlement("case_addons")
  const { caseComments, caseCommentsIsLoading, caseCommentsError } =
    useCaseComments({
      caseId,
      workspaceId,
      enabled: !repliesEnabled,
    })
  const {
    caseCommentThreads,
    caseCommentThreadsIsLoading,
    caseCommentThreadsError,
  } = useCaseCommentThreads({
    caseId,
    workspaceId,
    enabled: repliesEnabled,
  })
  const [editingCommentId, setEditingCommentId] = useState<string | null>(null)

  if (
    entitlementsLoading ||
    caseCommentThreadsIsLoading ||
    caseCommentsIsLoading
  ) {
    return (
      <div className="space-y-4 p-4">
        <CommentThreadSkeleton />
        <CommentThreadSkeleton />
      </div>
    )
  }

  if (caseCommentThreadsError || caseCommentsError) {
    return (
      <div className="flex items-center justify-center p-8">
        <div className="flex items-center gap-2 text-red-600">
          <AlertCircle className="h-4 w-4" />
          <span className="text-sm">Failed to load comments</span>
        </div>
      </div>
    )
  }

  return (
    <div className="mx-auto w-full space-y-4">
      <div className="space-y-3">
        {repliesEnabled
          ? caseCommentThreads?.map((thread) => (
              <CommentThread
                key={thread.comment.id}
                caseId={caseId}
                workspaceId={workspaceId}
                thread={thread}
                currentUserId={currentUser?.id ?? null}
                editingCommentId={editingCommentId}
                onEdit={(commentId) => setEditingCommentId(commentId)}
                onStopEditing={() => setEditingCommentId(null)}
                workflowSelectionEnabled={repliesEnabled}
              />
            ))
          : caseComments
              ?.filter((comment) => comment.parent_id === null)
              .map((comment) => (
                <CommentThreadShell key={comment.id}>
                  <CommentRow
                    caseId={caseId}
                    workspaceId={workspaceId}
                    comment={comment}
                    currentUserId={currentUser?.id ?? null}
                    isEditing={editingCommentId === comment.id}
                    onEdit={() => setEditingCommentId(comment.id)}
                    onStopEditing={() => setEditingCommentId(null)}
                  />
                </CommentThreadShell>
              ))}
      </div>
      <CommentComposer
        caseId={caseId}
        workspaceId={workspaceId}
        workflowSelectionEnabled={repliesEnabled}
      />
    </div>
  )
}

function CommentThreadShell({ children }: { children: React.ReactNode }) {
  return (
    <section
      className={cn(
        "overflow-hidden rounded-lg border border-border/60 px-5 py-4",
        INSET_SURFACE
      )}
    >
      {children}
    </section>
  )
}

function CommentThread({
  caseId,
  workspaceId,
  thread,
  currentUserId,
  editingCommentId,
  onEdit,
  onStopEditing,
  workflowSelectionEnabled,
}: {
  caseId: string
  workspaceId: string
  thread: CaseCommentThreadRead
  currentUserId: string | null
  editingCommentId: string | null
  onEdit: (commentId: string) => void
  onStopEditing: () => void
  workflowSelectionEnabled: boolean
}) {
  const { comment } = thread
  const replies = thread.replies ?? []
  const canReply = !comment.is_deleted
  const [repliesHidden, setRepliesHidden] = useState(false)
  const hasReplies = replies.length > 0

  return (
    <section
      className={cn(
        "overflow-hidden rounded-lg border border-border/60",
        INSET_SURFACE
      )}
    >
      <div className="px-5 py-4">
        <CommentRow
          caseId={caseId}
          workspaceId={workspaceId}
          comment={comment}
          currentUserId={currentUserId}
          isEditing={editingCommentId === comment.id}
          onEdit={() => onEdit(comment.id)}
          onStopEditing={onStopEditing}
          headerActions={
            hasReplies ? (
              <Button
                type="button"
                variant="ghost"
                size="icon"
                className="size-6 rounded-md text-muted-foreground hover:text-foreground"
                onClick={() => setRepliesHidden((hidden) => !hidden)}
              >
                {repliesHidden ? (
                  <ChevronDown className="size-4" />
                ) : (
                  <ChevronUp className="size-4" />
                )}
                <span className="sr-only">
                  {repliesHidden ? "Show replies" : "Hide replies"}
                </span>
              </Button>
            ) : null
          }
        />
      </div>

      {hasReplies && !repliesHidden && (
        <div className="border-t border-border/60">
          {replies.map((reply, index) => (
            <div
              key={reply.id}
              className={
                index === 0
                  ? "px-5 py-4"
                  : "border-t border-border/60 px-5 py-4"
              }
            >
              <CommentRow
                caseId={caseId}
                workspaceId={workspaceId}
                comment={reply}
                currentUserId={currentUserId}
                isEditing={editingCommentId === reply.id}
                onEdit={() => onEdit(reply.id)}
                onStopEditing={onStopEditing}
              />
            </div>
          ))}
        </div>
      )}

      {canReply && !repliesHidden ? (
        <div className="border-t border-border/60 px-5 py-3">
          <CommentComposer
            caseId={caseId}
            workspaceId={workspaceId}
            parentId={comment.id}
            placeholder="Leave a reply..."
            mode="inline"
            workflowSelectionEnabled={workflowSelectionEnabled}
          />
        </div>
      ) : null}
    </section>
  )
}

function CommentRow({
  caseId,
  workspaceId,
  comment,
  currentUserId,
  isEditing,
  onEdit,
  onStopEditing,
  headerActions,
}: {
  caseId: string
  workspaceId: string
  comment: CaseCommentRead
  currentUserId: string | null
  isEditing: boolean
  onEdit: () => void
  onStopEditing: () => void
  headerActions?: React.ReactNode
}) {
  const user = getCommentUser(comment)
  const isWorkflowComment = !!comment.workflow
  // A failed comment may never have started a run, so polling its execution
  // id would only produce 404s; render the badge from the persisted status.
  const persistedStatus = getWorkflowCommentStatus(comment)
  const { execution } = useCompactWorkflowExecution(
    persistedStatus === "failed"
      ? undefined
      : (comment.workflow?.wf_exec_id ?? undefined)
  )
  const workflowStatus = execution
    ? execution.status === "COMPLETED"
      ? "succeeded"
      : execution.status === "RUNNING"
        ? "running"
        : "failed"
    : persistedStatus
  const workflowRunPath = getWorkflowRunPath(
    workspaceId,
    execution?.id ?? comment.workflow?.wf_exec_id ?? null
  )
  const canManage = !comment.is_deleted && currentUserId === comment.user?.id
  // Only a bare `/Workflow` command saves with an empty body; the workflow
  // header stands alone in that case. The API rejects blank content otherwise.
  const hasBody = comment.content.trim() !== ""

  function renderBody() {
    if (isEditing) {
      return (
        <InlineCommentEdit
          comment={comment}
          caseId={caseId}
          workspaceId={workspaceId}
          onStopEditing={onStopEditing}
        />
      )
    }
    if (comment.is_deleted) {
      return (
        <p className="text-sm italic text-muted-foreground">Comment deleted</p>
      )
    }
    if (!hasBody) {
      return null
    }
    return (
      <ScrollArea className="w-full">
        <div className="min-w-0 text-sm leading-6">
          <CaseCommentViewer
            content={comment.content}
            workspaceId={workspaceId}
          />
        </div>
      </ScrollArea>
    )
  }

  return (
    <div className="group space-y-3">
      <div className="flex items-start gap-3">
        <div className="min-w-0 flex-1">
          {comment.is_deleted ? null : comment.agent ? (
            <CaseCommentAgentAttribution attribution={comment.agent} />
          ) : isWorkflowComment && comment.workflow ? (
            <div className="flex min-w-0 items-center gap-2">
              {getWorkflowStatusBadge(workflowStatus)}
              <span className="truncate text-sm font-medium text-foreground">
                {comment.workflow.title}
              </span>
              {comment.workflow.alias ? (
                <Badge
                  variant="outline"
                  className="h-5 shrink-0 rounded-full px-1.5 text-xs leading-none"
                >
                  {comment.workflow.alias}
                </Badge>
              ) : null}
              {workflowRunPath ? (
                <Button
                  asChild
                  type="button"
                  variant="ghost"
                  size="icon"
                  className="size-6 rounded-md text-muted-foreground hover:text-foreground"
                >
                  <Link href={workflowRunPath}>
                    <LinkIcon className="size-3.5" />
                    <span className="sr-only">Open workflow run</span>
                  </Link>
                </Button>
              ) : null}
            </div>
          ) : (
            <div className="flex min-w-0 items-center gap-2">
              <CaseUserAvatar user={user} size="sm" />
              <span className="truncate text-sm font-medium text-foreground">
                {user.getDisplayName()}
              </span>
            </div>
          )}
        </div>

        <div className="flex shrink-0 items-center gap-1 text-sm text-muted-foreground">
          <CaseEventTimestamp
            createdAt={comment.created_at}
            lastEditedAt={comment.last_edited_at}
          />
          {!isEditing && (headerActions || canManage) ? (
            <div className="flex items-center gap-1">
              {headerActions}
              {canManage ? (
                <CommentActionsWithEditing
                  caseId={caseId}
                  workspaceId={workspaceId}
                  comment={comment}
                  allowEdit={!isWorkflowComment}
                  onEdit={onEdit}
                />
              ) : null}
            </div>
          ) : null}
        </div>
      </div>

      {renderBody()}
      {!comment.is_deleted ? (
        <CaseCommentAgentInvocationList mentions={comment.mentions} />
      ) : null}
    </div>
  )
}

function CommentThreadSkeleton() {
  return (
    <div
      className={cn("rounded-lg border border-border/60 p-4", INSET_SURFACE)}
    >
      <div className="space-y-4">
        <div className="flex gap-3">
          <Skeleton className="size-4 rounded-full" />
          <div className="flex-1 space-y-2">
            <div className="flex items-center gap-2">
              <Skeleton className="h-4 w-24" />
              <Skeleton className="h-3 w-16" />
            </div>
            <Skeleton className="h-4 w-3/4" />
          </div>
        </div>
        <div className="border-t border-border/60 pt-4">
          <div className="flex gap-3">
            <Skeleton className="size-4 rounded-full" />
            <div className="flex-1 space-y-2">
              <div className="flex items-center gap-2">
                <Skeleton className="h-4 w-20" />
                <Skeleton className="h-3 w-12" />
              </div>
              <Skeleton className="h-4 w-1/2" />
            </div>
          </div>
        </div>
      </div>
    </div>
  )
}

/**
 * Enable pasting images into a plain comment `<Textarea>`.
 *
 * Pasted images are uploaded as case attachments and inserted as
 * `![name](attachment://...)` markdown at the cursor, preserving surrounding
 * text. Exposes an `isUploading` flag so callers can keep submit disabled.
 */
function useCommentImagePaste({
  caseId,
  workspaceId,
  form,
  textareaRef,
  adjustTextareaHeight,
  onSplice,
}: {
  caseId: string
  workspaceId: string
  form: UseFormReturn<CommentFormSchema>
  textareaRef: RefObject<HTMLTextAreaElement | null>
  adjustTextareaHeight: () => void
  /** Reports the edit so callers can keep mention offsets aligned. */
  onSplice?: (splice: TextSplice) => void
}) {
  const { uploadImage } = useCaseImageUpload(caseId, workspaceId)
  const [uploadingCount, setUploadingCount] = useState(0)

  const insertAtCursor = useCallback(
    (text: string) => {
      const textarea = textareaRef.current
      const current = form.getValues("content")
      const start = textarea?.selectionStart ?? current.length
      const end = textarea?.selectionEnd ?? current.length
      const next = current.slice(0, start) + text + current.slice(end)
      onSplice?.({ start, deleted: end - start, inserted: text.length })
      form.setValue("content", next, {
        shouldDirty: true,
        shouldValidate: true,
      })
      const cursor = start + text.length
      requestAnimationFrame(() => {
        const node = textareaRef.current
        if (node) {
          node.focus()
          node.setSelectionRange(cursor, cursor)
        }
        adjustTextareaHeight()
      })
    },
    [adjustTextareaHeight, form, onSplice, textareaRef]
  )

  const handlePaste = useCallback(
    async (event: React.ClipboardEvent<HTMLTextAreaElement>) => {
      const files = extractImageFiles(event.clipboardData)
      if (files.length === 0) {
        return
      }
      event.preventDefault()
      setUploadingCount((count) => count + files.length)
      for (const file of files) {
        try {
          const { src, fileName } = await uploadImage(
            createPastedImageFile(file)
          )
          insertAtCursor(`![${fileName}](${src})`)
        } catch {
          // Error toast is surfaced by uploadImage.
        } finally {
          setUploadingCount((count) => Math.max(0, count - 1))
        }
      }
    },
    [insertAtCursor, uploadImage]
  )

  return { handlePaste, isUploading: uploadingCount > 0 }
}

/**
 * Discoverability hint in the composer footer, shown only while the textarea
 * is focused and empty. Renders nothing when neither trigger applies.
 */
function ComposerHint({
  show,
  workflowsEnabled,
  agentsEnabled,
}: {
  show: boolean
  workflowsEnabled: boolean
  agentsEnabled: boolean
}) {
  if (!show || (!workflowsEnabled && !agentsEnabled)) {
    return null
  }
  return (
    <div
      className="flex items-center gap-3 text-xs text-muted-foreground"
      data-testid="composer-hint"
    >
      {workflowsEnabled ? (
        <span className="flex items-center gap-1">
          <Kbd>/</Kbd>
          Run workflow
        </span>
      ) : null}
      {agentsEnabled ? (
        <span className="flex items-center gap-1">
          <Kbd>@</Kbd>
          Mention agent
        </span>
      ) : null}
    </div>
  )
}

function CommentComposer({
  caseId,
  workspaceId,
  parentId,
  placeholder = "Leave a comment...",
  mode = "default",
  onSubmitted,
  autoFocus = false,
  workflowSelectionEnabled = false,
}: {
  caseId: string
  workspaceId: string
  parentId?: string
  placeholder?: string
  mode?: "default" | "inline"
  onSubmitted?: () => void
  autoFocus?: boolean
  workflowSelectionEnabled?: boolean
}) {
  const { createComment, createCommentIsPending } = useCreateCaseComment({
    caseId,
    workspaceId,
  })
  const isInline = mode === "inline"
  // Shared by the textarea and its highlight overlay. If these drift apart the
  // two layers wrap differently and the caret stops matching the visible text.
  const textMetricsClassName = isInline
    ? "px-0 py-1 text-sm"
    : "px-0 py-0 text-sm"
  const textareaRef = useRef<HTMLTextAreaElement | null>(null)
  const [isFocused, setIsFocused] = useState(false)
  const form = useForm<CommentFormSchema>({
    resolver: zodResolver(commentFormSchema),
    defaultValues: {
      content: "",
    },
    mode: "onSubmit",
  })

  const adjustTextareaHeight = useCallback(() => {
    const textarea = textareaRef.current
    if (!textarea) return

    textarea.style.height = "auto"
    textarea.style.height = `${Math.max(textarea.scrollHeight, isInline ? 36 : 72)}px`
    textarea.style.overflowY = "hidden"
  }, [isInline])

  // Mentions live as display text (`@Label`, `/Label`) in the textarea; the
  // hook maps them back to the wire value on submit.
  const getContent = useCallback(() => form.getValues("content"), [form])
  const setContent = useCallback(
    (next: string) => {
      form.setValue("content", next, {
        shouldDirty: true,
        shouldValidate: true,
      })
    },
    [form]
  )
  const mentions = useCommentMentions({
    workspaceId,
    textareaRef,
    getText: getContent,
    setText: setContent,
    workflowsEnabled: workflowSelectionEnabled,
  })

  const { handlePaste, isUploading: imageUploading } = useCommentImagePaste({
    caseId,
    workspaceId,
    form,
    textareaRef,
    adjustTextareaHeight,
    onSplice: mentions.applySplice,
  })

  const content = form.watch("content")
  const trimmedContent = content.trim()

  useLayoutEffect(() => {
    adjustTextareaHeight()
  }, [adjustTextareaHeight, content])

  const handleSubmit = async (values: CommentFormSchema) => {
    const workflowId = mentions.workflowId
    const nextContent = mentions.serialize(values.content).trim()
    // A bare `/Workflow` command still runs and saves with an empty body.
    if (!nextContent && !workflowId) {
      return
    }
    // The length limit applies to what the API receives, not the shorter
    // display text, so check the serialized value.
    const serialized = commentWireSchema.safeParse(nextContent)
    if (!serialized.success) {
      form.setError("content", {
        message: serialized.error.issues[0]?.message,
      })
      return
    }
    try {
      await createComment({
        content: nextContent,
        parent_id: parentId,
        ...(workflowId ? { workflow_id: workflowId } : {}),
      })
      form.reset({ content: "" })
      mentions.reset()
      onSubmitted?.()
    } catch (error) {
      console.error("Error creating comment:", error)
    }
  }

  const handleKeyDown = (event: React.KeyboardEvent<HTMLTextAreaElement>) => {
    // The mention layer owns popover keys and atomic mention backspace.
    if (mentions.handleKeyDown(event)) {
      return
    }
    if (event.key === "Enter" && (event.metaKey || event.ctrlKey)) {
      event.preventDefault()
      if (createCommentIsPending || imageUploading) {
        return
      }
      form.handleSubmit(handleSubmit)()
    }
  }

  return (
    <div
      className={cn(
        isInline
          ? "w-full"
          : ["rounded-lg border border-border/60 px-4 py-3", INSET_SURFACE]
      )}
    >
      <Form {...form}>
        <form
          onSubmit={form.handleSubmit(handleSubmit)}
          className="flex flex-col gap-2"
        >
          <FormField
            control={form.control}
            name="content"
            render={({ field }) => (
              <FormItem>
                <CommentMentionPopover
                  open={mentions.isOpen}
                  kind={mentions.kind}
                  caret={mentions.caret}
                  sections={mentions.sections}
                  itemCount={mentions.itemCount}
                  activeIndex={mentions.activeIndex}
                  isLoading={mentions.isLoading}
                  onSelect={mentions.selectSuggestion}
                >
                  <CommentMentionOverlay
                    text={content}
                    mentions={mentions.ranges}
                    className={textMetricsClassName}
                  />
                  <FormControl>
                    <Textarea
                      autoFocus={autoFocus}
                      ref={(node) => {
                        field.ref(node)
                        textareaRef.current = node
                      }}
                      // Text is painted by the overlay behind, so only the
                      // caret stays visible here.
                      className={cn(
                        "relative resize-none border-none bg-transparent text-transparent caret-foreground shadow-none focus-visible:ring-0",
                        textMetricsClassName,
                        isInline ? "min-h-9" : "min-h-[72px]"
                      )}
                      name={field.name}
                      onBlur={() => {
                        field.onBlur()
                        setIsFocused(false)
                        mentions.dismiss()
                      }}
                      onFocus={() => setIsFocused(true)}
                      onChange={(event) => {
                        mentions.handleTextChange(
                          event.target.value,
                          event.target.selectionStart ??
                            event.target.value.length
                        )
                        field.onChange(event)
                        adjustTextareaHeight()
                      }}
                      onKeyDown={handleKeyDown}
                      onSelect={mentions.handleSelectionChange}
                      onPaste={(event) => void handlePaste(event)}
                      placeholder={placeholder}
                      value={field.value}
                    />
                  </FormControl>
                </CommentMentionPopover>
                <FormMessage />
              </FormItem>
            )}
          />

          <div className="flex items-end justify-between gap-2">
            <ComposerHint
              show={isFocused && !trimmedContent}
              workflowsEnabled={mentions.workflowsEnabled}
              agentsEnabled={mentions.agentsEnabled}
            />
            <div className="ml-auto flex items-center gap-2">
              {imageUploading ? (
                <span className="text-xs text-muted-foreground">
                  Uploading image…
                </span>
              ) : null}
              <Button
                type="submit"
                variant="outline"
                size="icon"
                className="size-7 shrink-0 rounded-full border-border/70"
                disabled={
                  createCommentIsPending || imageUploading || !trimmedContent
                }
                aria-label="Send"
              >
                <ArrowUpIcon className="size-3.5" />
                <span className="sr-only">Send</span>
              </Button>
            </div>
          </div>
        </form>
      </Form>
    </div>
  )
}

function InlineCommentEdit({
  comment,
  caseId,
  workspaceId,
  onStopEditing,
}: {
  comment: CaseCommentRead
  caseId: string
  workspaceId: string
  onStopEditing: () => void
}) {
  const { updateComment, updateCommentIsPending } = useUpdateCaseComment({
    caseId,
    workspaceId,
    commentId: comment.id,
  })
  const textareaRef = useRef<HTMLTextAreaElement | null>(null)
  const form = useForm<CommentFormSchema>({
    resolver: zodResolver(commentEditFormSchema),
    defaultValues: {
      content: comment.content,
    },
  })

  const adjustTextareaHeight = useCallback(() => {
    const textarea = textareaRef.current
    if (!textarea) return

    textarea.style.height = "auto"
    textarea.style.height = `${Math.max(textarea.scrollHeight, 72)}px`
    textarea.style.overflowY = "hidden"
  }, [])

  const { handlePaste, isUploading: imageUploading } = useCommentImagePaste({
    caseId,
    workspaceId,
    form,
    textareaRef,
    adjustTextareaHeight,
  })

  const content = form.watch("content")

  useLayoutEffect(() => {
    adjustTextareaHeight()
  }, [adjustTextareaHeight, content])

  const handleSubmit = async (values: CommentFormSchema) => {
    try {
      await updateComment({
        content: values.content,
      })
      onStopEditing()
      toast({
        title: "Comment updated",
        description: "Your comment has been updated successfully.",
      })
    } catch (error) {
      console.error("Error updating comment:", error)
    }
  }

  const handleKeyDown = (event: React.KeyboardEvent) => {
    if (event.key === "Enter" && (event.metaKey || event.ctrlKey)) {
      event.preventDefault()
      if (updateCommentIsPending || imageUploading) {
        return
      }
      form.handleSubmit(handleSubmit)()
    }
  }

  return (
    <Form {...form}>
      <form onSubmit={form.handleSubmit(handleSubmit)} className="space-y-3">
        <FormField
          control={form.control}
          name="content"
          render={({ field }) => (
            <FormItem>
              <FormControl>
                <Textarea
                  autoFocus
                  ref={(node) => {
                    field.ref(node)
                    textareaRef.current = node
                  }}
                  className="min-h-[72px] border-none px-0 py-0 text-sm shadow-none focus-visible:ring-0"
                  name={field.name}
                  onBlur={field.onBlur}
                  onChange={(event) => {
                    field.onChange(event)
                    adjustTextareaHeight()
                  }}
                  onKeyDown={handleKeyDown}
                  onPaste={(event) => void handlePaste(event)}
                  value={field.value}
                />
              </FormControl>
              <FormMessage />
            </FormItem>
          )}
        />

        <div className="flex items-center justify-end gap-2">
          {imageUploading ? (
            <span className="mr-auto text-xs text-muted-foreground">
              Uploading image…
            </span>
          ) : null}
          <Button
            type="button"
            variant="ghost"
            size="sm"
            className="h-7 px-2 text-xs"
            onClick={onStopEditing}
          >
            Cancel
          </Button>
          <Button
            type="submit"
            size="sm"
            className="h-7 px-2 text-xs"
            disabled={
              updateCommentIsPending || imageUploading || !content.trim()
            }
          >
            Save
          </Button>
        </div>
      </form>
    </Form>
  )
}

function CommentActionsWithEditing({
  caseId,
  workspaceId,
  comment,
  allowEdit,
  onEdit,
}: {
  caseId: string
  workspaceId: string
  comment: CaseCommentRead
  allowEdit: boolean
  onEdit: () => void
}) {
  const [showDeleteConfirm, setShowDeleteConfirm] = useState(false)
  const { deleteComment } = useDeleteCaseComment({
    caseId,
    workspaceId,
    commentId: comment.id,
  })

  const handleDelete = async () => {
    try {
      await deleteComment()
      toast({
        title: "Comment deleted",
        description: "Your comment has been deleted successfully.",
      })
    } catch (error) {
      console.error("Error deleting comment:", error)
    }
  }

  return (
    <>
      <DropdownMenu>
        <DropdownMenuTrigger asChild>
          <Button
            variant="ghost"
            size="icon"
            className="size-6 rounded-md text-muted-foreground hover:text-foreground"
          >
            <MoreHorizontal className="size-4" />
            <span className="sr-only">More options</span>
          </Button>
        </DropdownMenuTrigger>
        <DropdownMenuContent align="end">
          {allowEdit ? (
            <DropdownMenuItem
              className="flex cursor-pointer items-center gap-2 text-xs"
              onClick={onEdit}
            >
              <PencilIcon className="size-3" />
              Edit
            </DropdownMenuItem>
          ) : null}
          <DropdownMenuItem
            className="flex cursor-pointer items-center gap-2 text-xs text-destructive focus:text-destructive"
            onClick={() => setShowDeleteConfirm(true)}
          >
            <Trash2Icon className="size-3" />
            Delete
          </DropdownMenuItem>
        </DropdownMenuContent>
      </DropdownMenu>

      <AlertDialog open={showDeleteConfirm} onOpenChange={setShowDeleteConfirm}>
        <AlertDialogContent>
          <AlertDialogHeader>
            <AlertDialogTitle>Delete this comment?</AlertDialogTitle>
            <AlertDialogDescription>
              You cannot undo this action.
            </AlertDialogDescription>
          </AlertDialogHeader>
          <AlertDialogFooter>
            <AlertDialogCancel>Cancel</AlertDialogCancel>
            <AlertDialogAction
              variant="destructive"
              onClick={() => {
                handleDelete()
                setShowDeleteConfirm(false)
              }}
            >
              Delete
            </AlertDialogAction>
          </AlertDialogFooter>
        </AlertDialogContent>
      </AlertDialog>
    </>
  )
}
