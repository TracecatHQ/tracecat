"use client"

import { zodResolver } from "@hookform/resolvers/zod"
import type { Editor } from "@tiptap/react"
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
import { useCallback, useEffect, useState } from "react"
import { useForm } from "react-hook-form"
import { z } from "zod"
import type { CaseCommentRead, CaseCommentThreadRead } from "@/client"
import {
  CaseCommentAgentAttribution,
  CaseCommentAgentInvocationList,
} from "@/components/cases/case-comment-agent"
import { CaseCommentEditor } from "@/components/cases/case-comment-editor"
import { CaseCommentViewer } from "@/components/cases/case-description-editor"
import {
  CaseEventTimestamp,
  CaseUserAvatar,
} from "@/components/cases/case-panel-common"
import { MentionHint } from "@/components/mentions/mention-hint"
import { MentionPopover } from "@/components/mentions/mention-popover"
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
import { Form, FormField, FormItem, FormMessage } from "@/components/ui/form"
import { ScrollArea } from "@/components/ui/scroll-area"
import { Skeleton } from "@/components/ui/skeleton"
import { toast } from "@/components/ui/use-toast"
import { useAuth } from "@/hooks/use-auth"
import { useEntitlements } from "@/hooks/use-entitlements"
import { useTiptapMentions } from "@/hooks/use-tiptap-mentions"
import { SYSTEM_USER_READ, User } from "@/lib/auth"
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
      <CommentComposer caseId={caseId} workspaceId={workspaceId} />
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
}: {
  caseId: string
  workspaceId: string
  thread: CaseCommentThreadRead
  currentUserId: string | null
  editingCommentId: string | null
  onEdit: (commentId: string) => void
  onStopEditing: () => void
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

function CommentComposer({
  caseId,
  workspaceId,
  parentId,
  placeholder = "Leave a comment...",
  mode = "default",
  onSubmitted,
  autoFocus = false,
}: {
  caseId: string
  workspaceId: string
  parentId?: string
  placeholder?: string
  mode?: "default" | "inline"
  onSubmitted?: () => void
  autoFocus?: boolean
}) {
  const { createComment, createCommentIsPending } = useCreateCaseComment({
    caseId,
    workspaceId,
  })
  const isInline = mode === "inline"
  const [editor, setEditor] = useState<Editor | null>(null)
  const [isFocused, setIsFocused] = useState(false)
  const [imageUploading, setImageUploading] = useState(false)
  const form = useForm<CommentFormSchema>({
    resolver: zodResolver(commentFormSchema),
    defaultValues: {
      content: "",
    },
    mode: "onSubmit",
  })
  const mentions = useTiptapMentions({
    editor,
    workspaceId,
    // A comment may invoke several agents, but runs at most one workflow.
    agents: { entitlements: ["agent_addons", "case_addons"] },
    workflows: { entitlements: ["case_addons"], single: true },
  })

  const content = form.watch("content")
  const trimmedContent = content.trim()
  const serializeMentions = mentions.serialize
  const resetMentions = mentions.reset
  const handleSubmit = useCallback(
    async (values: CommentFormSchema) => {
      const serializedComment = serializeMentions(values.content)
      const nextContent = serializedComment.content.trim()
      const workflowId = serializedComment.workflowId
      // A bare `/Workflow` command still runs and saves with an empty body.
      if (!nextContent && !workflowId) {
        return
      }
      // Validate after removing the transient workflow marker so the limit
      // applies to the exact Markdown sent over the wire.
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
        resetMentions()
        onSubmitted?.()
      } catch (error) {
        console.error("Error creating comment:", error)
      }
    },
    [
      createComment,
      form,
      onSubmitted,
      parentId,
      resetMentions,
      serializeMentions,
    ]
  )

  const handleMentionKeyDown = mentions.handleKeyDown
  useEffect(() => {
    if (!editor) {
      return
    }
    const handleKeyDown = (event: KeyboardEvent) => {
      if (handleMentionKeyDown(event)) {
        event.stopPropagation()
        return
      }
      if (
        event.key === "Enter" &&
        (event.metaKey || event.ctrlKey) &&
        !event.isComposing
      ) {
        event.preventDefault()
        event.stopPropagation()
        if (createCommentIsPending || imageUploading) {
          return
        }
        void form.handleSubmit(handleSubmit)()
      }
    }
    const editorElement = editor.view.dom
    editorElement.addEventListener("keydown", handleKeyDown, true)
    return () => {
      editorElement.removeEventListener("keydown", handleKeyDown, true)
    }
  }, [
    createCommentIsPending,
    editor,
    form,
    handleMentionKeyDown,
    handleSubmit,
    imageUploading,
  ])

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
                <MentionPopover
                  open={mentions.isOpen}
                  kind={mentions.kind}
                  caret={mentions.caret}
                  sections={mentions.sections}
                  itemCount={mentions.itemCount}
                  activeIndex={mentions.activeIndex}
                  isLoading={mentions.isLoading}
                  locked={mentions.locked}
                  hasError={mentions.hasError}
                  onSelect={mentions.selectSuggestion}
                >
                  <CaseCommentEditor
                    value={field.value}
                    onChange={field.onChange}
                    caseId={caseId}
                    workspaceId={workspaceId}
                    placeholder={placeholder}
                    mode={mode}
                    autoFocus={autoFocus}
                    onBlur={() => {
                      field.onBlur()
                      setIsFocused(false)
                      mentions.dismiss()
                    }}
                    onFocus={() => setIsFocused(true)}
                    onUploadingChange={setImageUploading}
                    onEditorReady={setEditor}
                  />
                </MentionPopover>
                <FormMessage />
              </FormItem>
            )}
          />

          <div className="flex items-end justify-between gap-2">
            <MentionHint
              show={isFocused && !trimmedContent}
              agents={mentions.agents}
              workflows={mentions.workflows}
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
  const [editor, setEditor] = useState<Editor | null>(null)
  const [imageUploading, setImageUploading] = useState(false)
  const form = useForm<CommentFormSchema>({
    resolver: zodResolver(commentEditFormSchema),
    defaultValues: {
      content: comment.content,
    },
  })

  const content = form.watch("content")
  const handleSubmit = useCallback(
    async (values: CommentFormSchema) => {
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
    },
    [onStopEditing, updateComment]
  )

  useEffect(() => {
    if (!editor) {
      return
    }
    const handleKeyDown = (event: KeyboardEvent) => {
      if (
        event.key !== "Enter" ||
        (!event.metaKey && !event.ctrlKey) ||
        event.isComposing
      ) {
        return
      }
      event.preventDefault()
      event.stopPropagation()
      if (updateCommentIsPending || imageUploading) {
        return
      }
      void form.handleSubmit(handleSubmit)()
    }
    const editorElement = editor.view.dom
    editorElement.addEventListener("keydown", handleKeyDown, true)
    return () => {
      editorElement.removeEventListener("keydown", handleKeyDown, true)
    }
  }, [editor, form, handleSubmit, imageUploading, updateCommentIsPending])

  return (
    <Form {...form}>
      <form onSubmit={form.handleSubmit(handleSubmit)} className="space-y-3">
        <FormField
          control={form.control}
          name="content"
          render={({ field }) => (
            <FormItem>
              <CaseCommentEditor
                value={field.value}
                onChange={field.onChange}
                caseId={caseId}
                workspaceId={workspaceId}
                placeholder="Edit comment..."
                autoFocus
                onBlur={field.onBlur}
                onUploadingChange={setImageUploading}
                onEditorReady={setEditor}
              />
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
