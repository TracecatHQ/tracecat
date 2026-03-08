"use client"

import { zodResolver } from "@hookform/resolvers/zod"
import {
  AlertCircle,
  ArrowUpIcon,
  MoreHorizontal,
  PencilIcon,
  Trash2Icon,
} from "lucide-react"
import type React from "react"
import { useCallback, useLayoutEffect, useRef, useState } from "react"
import { useForm } from "react-hook-form"
import { z } from "zod"
import type { CaseCommentRead, CaseCommentThreadRead } from "@/client"
import { CaseCommentViewer } from "@/components/cases/case-description-editor"
import {
  CaseEventTimestamp,
  CaseUserAvatar,
} from "@/components/cases/case-panel-common"
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
import { Skeleton } from "@/components/ui/skeleton"
import { Textarea } from "@/components/ui/textarea"
import { toast } from "@/components/ui/use-toast"
import { useAuth } from "@/hooks/use-auth"
import { SYSTEM_USER_READ, User } from "@/lib/auth"
import {
  useCaseCommentThreads,
  useCreateCaseComment,
  useDeleteCaseComment,
  useUpdateCaseComment,
} from "@/lib/hooks"

const commentFormSchema = z.object({
  content: z
    .string()
    .min(1, { message: "Comment cannot be empty" })
    .max(25000, { message: "Comment cannot be longer than 25000 characters" }),
})

type CommentFormSchema = z.infer<typeof commentFormSchema>

function getCommentUser(comment: CaseCommentRead) {
  return new User(comment.user ?? SYSTEM_USER_READ)
}

export function CommentSection({
  caseId,
  workspaceId,
}: {
  caseId: string
  workspaceId: string
}) {
  const { user: currentUser } = useAuth()
  const {
    caseCommentThreads,
    caseCommentThreadsIsLoading,
    caseCommentThreadsError,
  } = useCaseCommentThreads({
    caseId,
    workspaceId,
  })
  const [editingCommentId, setEditingCommentId] = useState<string | null>(null)

  if (caseCommentThreadsIsLoading) {
    return (
      <div className="space-y-4 p-4">
        <CommentThreadSkeleton />
        <CommentThreadSkeleton />
      </div>
    )
  }

  if (caseCommentThreadsError) {
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
        {caseCommentThreads?.map((thread) => (
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
        ))}
      </div>
      <CommentComposer caseId={caseId} workspaceId={workspaceId} />
    </div>
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

  return (
    <section className="overflow-hidden rounded-lg border border-border/60">
      <div className="px-5 py-4">
        <CommentRow
          caseId={caseId}
          workspaceId={workspaceId}
          comment={comment}
          currentUserId={currentUserId}
          isEditing={editingCommentId === comment.id}
          onEdit={() => onEdit(comment.id)}
          onStopEditing={onStopEditing}
        />
      </div>

      {replies.length > 0 && (
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

      {canReply ? (
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
}: {
  caseId: string
  workspaceId: string
  comment: CaseCommentRead
  currentUserId: string | null
  isEditing: boolean
  onEdit: () => void
  onStopEditing: () => void
}) {
  const user = getCommentUser(comment)
  const canManage = !comment.is_deleted && currentUserId === comment.user?.id
  const contentInsetClass = comment.is_deleted ? "" : "pl-7"

  return (
    <div className="group space-y-3">
      <div className="flex items-start justify-between gap-3">
        {comment.is_deleted ? (
          <div className="flex min-w-0 flex-1 items-center text-sm text-muted-foreground">
            <CaseEventTimestamp
              createdAt={comment.created_at}
              lastEditedAt={comment.last_edited_at}
            />
          </div>
        ) : (
          <div className="flex min-w-0 flex-1 items-center gap-2">
            <CaseUserAvatar user={user} size="sm" />
            <span className="truncate text-sm font-medium text-foreground">
              {user.getDisplayName()}
            </span>
            <CaseEventTimestamp
              createdAt={comment.created_at}
              lastEditedAt={comment.last_edited_at}
            />
          </div>
        )}

        {!isEditing && canManage && (
          <div className="flex items-center gap-1">
            <CommentActionsWithEditing
              caseId={caseId}
              workspaceId={workspaceId}
              comment={comment}
              onEdit={onEdit}
            />
          </div>
        )}
      </div>

      {isEditing ? (
        <div className={contentInsetClass}>
          <InlineCommentEdit
            comment={comment}
            caseId={caseId}
            workspaceId={workspaceId}
            onStopEditing={onStopEditing}
          />
        </div>
      ) : comment.is_deleted ? (
        <p className="text-sm italic text-muted-foreground">Comment deleted</p>
      ) : (
        <div
          className={`overflow-x-auto text-sm leading-6 ${contentInsetClass}`}
        >
          <CaseCommentViewer content={comment.content} />
        </div>
      )}
    </div>
  )
}

function CommentThreadSkeleton() {
  return (
    <div className="rounded-lg border border-border/60 p-4">
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
  const textareaRef = useRef<HTMLTextAreaElement | null>(null)
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

  const content = form.watch("content")

  useLayoutEffect(() => {
    adjustTextareaHeight()
  }, [adjustTextareaHeight, content])

  const handleSubmit = async (values: CommentFormSchema) => {
    try {
      await createComment({
        content: values.content,
        parent_id: parentId,
      })
      form.reset({ content: "" })
      onSubmitted?.()
    } catch (error) {
      console.error("Error creating comment:", error)
    }
  }

  const handleKeyDown = (event: React.KeyboardEvent) => {
    if (event.key === "Enter" && (event.metaKey || event.ctrlKey)) {
      event.preventDefault()
      form.handleSubmit(handleSubmit)()
    }
  }

  return (
    <div
      className={
        isInline ? "w-full" : "rounded-lg border border-border/60 px-4 py-3"
      }
    >
      <Form {...form}>
        <form onSubmit={form.handleSubmit(handleSubmit)} className="relative">
          <FormField
            control={form.control}
            name="content"
            render={({ field }) => (
              <FormItem>
                <FormControl>
                  <Textarea
                    autoFocus={autoFocus}
                    ref={(node) => {
                      field.ref(node)
                      textareaRef.current = node
                    }}
                    className={
                      isInline
                        ? "min-h-9 resize-none border-none px-0 py-2 pr-10 text-sm shadow-none focus-visible:ring-0"
                        : "min-h-[72px] resize-none border-none px-0 py-0 pr-10 text-sm shadow-none focus-visible:ring-0"
                    }
                    name={field.name}
                    onBlur={field.onBlur}
                    onChange={(event) => {
                      field.onChange(event)
                      adjustTextareaHeight()
                    }}
                    onKeyDown={handleKeyDown}
                    placeholder={placeholder}
                    value={field.value}
                  />
                </FormControl>
                <FormMessage />
              </FormItem>
            )}
          />

          <div className="pointer-events-none absolute bottom-0 right-0 flex items-end">
            <Button
              type="submit"
              variant="outline"
              size="icon"
              className="pointer-events-auto size-7 rounded-full border-border/70"
              disabled={createCommentIsPending || !content.trim()}
              aria-label="Send"
            >
              <ArrowUpIcon className="size-3.5" />
              <span className="sr-only">Send</span>
            </Button>
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
    resolver: zodResolver(commentFormSchema),
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
                  value={field.value}
                />
              </FormControl>
              <FormMessage />
            </FormItem>
          )}
        />

        <div className="flex items-center justify-end gap-2">
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
            disabled={updateCommentIsPending || !content.trim()}
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
  onEdit,
}: {
  caseId: string
  workspaceId: string
  comment: CaseCommentRead
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
          <DropdownMenuItem
            className="flex cursor-pointer items-center gap-2 text-xs"
            onClick={onEdit}
          >
            <PencilIcon className="size-3" />
            Edit
          </DropdownMenuItem>
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
