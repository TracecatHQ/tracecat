"use client"

import { zodResolver } from "@hookform/resolvers/zod"
import {
  AlertCircle,
  ArrowUpIcon,
  MoreHorizontal,
  PaperclipIcon,
  PencilIcon,
  Trash2Icon,
} from "lucide-react"
import type React from "react"
import { useCallback, useLayoutEffect, useRef, useState } from "react"
import { useForm } from "react-hook-form"
import { z } from "zod"
import type { CaseCommentRead } from "@/client"
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
import { SYSTEM_USER_READ, User } from "@/lib/auth"
import {
  useCaseComments,
  useCreateCaseComment,
  useDeleteCaseComment,
  useUpdateCaseComment,
} from "@/lib/hooks"

export function CommentSection({
  caseId,
  workspaceId,
}: {
  caseId: string
  workspaceId: string
}) {
  const { caseComments, caseCommentsIsLoading, caseCommentsError } =
    useCaseComments({
      caseId,
      workspaceId,
    })

  const [editingCommentId, setEditingCommentId] = useState<string | null>(null)

  if (caseCommentsIsLoading) {
    return (
      <div className="space-y-4 p-4">
        <CommentSkeleton />
        <CommentSkeleton />
        <CommentSkeleton />
      </div>
    )
  }

  if (caseCommentsError) {
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
    <div className="mx-auto w-full">
      <div className="bg-card">
        <div className="space-y-3">
          {caseComments?.map((comment) => {
            const user = new User(comment.user ?? SYSTEM_USER_READ)
            const displayName = user.getDisplayName()
            const isEditing = editingCommentId === comment.id

            return (
              <div key={comment.id} className="group">
                <div className="rounded-lg border border-border/40 bg-background p-4 shadow-sm transition-colors hover:border-muted-foreground/40 focus-within:border-muted-foreground/40">
                  {/* Two-row layout: Row 1 – avatar, name, timestamp & actions. Row 2 – comment content */}
                  <div className="space-y-3">
                    {/* Row 1 */}
                    <div className="flex items-center justify-between gap-2">
                      <div className="flex items-center gap-2">
                        <CaseUserAvatar user={user} size="sm" />
                        {/* User display name */}
                        <span className="text-sm font-medium text-foreground">
                          {displayName}
                        </span>
                        <CaseEventTimestamp
                          createdAt={comment.created_at}
                          lastEditedAt={comment.last_edited_at}
                        />
                      </div>
                      {!isEditing && (
                        <CommentActionsWithEditing
                          caseId={caseId}
                          workspaceId={workspaceId}
                          comment={comment}
                          onEdit={() => setEditingCommentId(comment.id)}
                        />
                      )}
                    </div>

                    {/* Row 2 */}
                    {isEditing ? (
                      <InlineCommentEdit
                        comment={comment}
                        caseId={caseId}
                        workspaceId={workspaceId}
                        onStopEditing={() => setEditingCommentId(null)}
                      />
                    ) : (
                      <CaseCommentViewer content={comment.content} />
                    )}
                  </div>
                </div>
              </div>
            )
          })}
        </div>
        <div className="mt-3">
          <CommentTextBox caseId={caseId} workspaceId={workspaceId} />
        </div>
      </div>
    </div>
  )
}

function CommentSkeleton() {
  return (
    <div className="flex gap-3">
      <Skeleton className="size-8 rounded-full" />
      <div className="flex-1 space-y-2">
        <div className="flex items-center gap-2">
          <Skeleton className="h-4 w-24" />
          <Skeleton className="h-3 w-16" />
        </div>
        <Skeleton className="h-4 w-3/4" />
        <Skeleton className="h-4 w-1/2" />
      </div>
    </div>
  )
}

const commentFormSchema = z.object({
  content: z
    .string()
    .min(1, { message: "Comment cannot be empty" })
    .max(5000, { message: "Comment cannot be longer than 5000 characters" }),
})
type CommentFormSchema = z.infer<typeof commentFormSchema>

function CommentTextBox({
  caseId,
  workspaceId,
}: {
  caseId: string
  workspaceId: string
}) {
  const { createComment } = useCreateCaseComment({
    caseId,
    workspaceId,
  })
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

    const minHeight = 60
    const nextHeight = Math.max(textarea.scrollHeight, minHeight)

    textarea.style.height = `${nextHeight}px`
    textarea.style.overflowY = "hidden"
  }, [])

  const contentValue = form.watch("content")

  useLayoutEffect(() => {
    adjustTextareaHeight()
  }, [contentValue, adjustTextareaHeight])

  const handleKeyDown = (e: React.KeyboardEvent) => {
    // Only submit if Cmd+Enter or Ctrl+Enter is pressed
    if (e.key === "Enter" && (e.metaKey || e.ctrlKey)) {
      e.preventDefault()
      form.handleSubmit(handleCommentSubmit)()
    }
    // Regular Enter will create newlines by default (no special handling needed)
  }

  const handleCommentSubmit = async (values: CommentFormSchema) => {
    // Comments not implemented on backend yet
    try {
      await createComment({
        content: values.content,
      })
      form.reset({ content: "" })
    } catch (error) {
      console.error(error)
    }
  }
  return (
    <div className="flex w-full items-end gap-2">
      {/* Match styling of ChatInput */}
      <div className="relative flex w-full gap-2 rounded-md border border-border/40 bg-background px-4 shadow-sm transition-colors hover:border-muted-foreground/40 focus-within:border-muted-foreground/40">
        <Form {...form}>
          <form
            onSubmit={form.handleSubmit(handleCommentSubmit)}
            className="flex w-full space-x-2"
          >
            <FormField
              control={form.control}
              name="content"
              render={({ field }) => (
                <FormItem className="w-full">
                  <FormControl>
                    <Textarea
                      ref={(node) => {
                        field.ref(node)
                        textareaRef.current = node
                      }}
                      name={field.name}
                      onBlur={field.onBlur}
                      placeholder="Leave a comment..."
                      className="shadow-none w-full resize-none border-none min-h-[60px] pl-0 pr-16 placeholder:text-muted-foreground focus-visible:ring-0"
                      value={field.value}
                      onChange={(event) => {
                        field.onChange(event)
                        adjustTextareaHeight()
                      }}
                      onKeyDown={handleKeyDown}
                    />
                  </FormControl>
                </FormItem>
              )}
            />
            <div className="absolute bottom-2 right-2 flex gap-1.5">
              <Button
                variant="ghost"
                size="icon"
                className="size-7 rounded-md text-gray-400 hover:bg-gray-100 hover:text-muted-foreground"
                disabled
              >
                <PaperclipIcon className="size-4" />
                <span className="sr-only">Attach file</span>
              </Button>
              <Button
                variant="ghost"
                size="icon"
                type="submit"
                className="size-7 rounded-md text-gray-400 hover:bg-gray-200/80 hover:text-muted-foreground"
                disabled={!form.watch("content").trim()}
              >
                <ArrowUpIcon className="size-4" />
                <span className="sr-only">Send comment</span>
              </Button>
            </div>
          </form>
        </Form>
      </div>
    </div>
  )
}

// New component for inline comment editing
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
  const form = useForm<CommentFormSchema>({
    resolver: zodResolver(commentFormSchema),
    defaultValues: {
      content: comment.content,
    },
  })
  const textareaRef = useRef<HTMLTextAreaElement | null>(null)

  const { updateComment } = useUpdateCaseComment({
    caseId,
    workspaceId,
    commentId: comment.id,
  })

  const adjustTextareaHeight = useCallback(() => {
    const textarea = textareaRef.current
    if (!textarea) return

    textarea.style.height = "auto"

    const minHeight = 60
    const nextHeight = Math.max(textarea.scrollHeight, minHeight)

    textarea.style.height = `${nextHeight}px`
    textarea.style.overflowY = "hidden"
  }, [])

  const contentValue = form.watch("content")

  useLayoutEffect(() => {
    adjustTextareaHeight()
  }, [contentValue, adjustTextareaHeight])

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

  const handleKeyDown = (e: React.KeyboardEvent) => {
    // Save on Cmd+Enter or Ctrl+Enter
    if (e.key === "Enter" && (e.metaKey || e.ctrlKey)) {
      e.preventDefault()
      form.handleSubmit(handleSubmit)()
    }
  }

  return (
    <Form {...form}>
      <form onSubmit={form.handleSubmit(handleSubmit)} className="mt-2">
        <div className="relative rounded-md">
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
                    name={field.name}
                    onBlur={field.onBlur}
                    className="min-h-[60px] w-full resize-none rounded-md border-none bg-transparent pl-0 pr-20 shadow-none placeholder:text-muted-foreground focus-visible:ring-0"
                    onChange={(event) => {
                      field.onChange(event)
                      adjustTextareaHeight()
                    }}
                    onKeyDown={handleKeyDown}
                    value={field.value}
                  />
                </FormControl>
                <FormMessage className="px-3" />
              </FormItem>
            )}
          />
          <div className="absolute bottom-2 right-2 flex gap-1.5">
            <Button
              type="button"
              variant="ghost"
              size="sm"
              className="h-6 px-2 text-xs"
              onClick={onStopEditing}
            >
              Cancel
            </Button>
            <Button type="submit" size="sm" className="h-6 px-2 text-xs">
              Save
            </Button>
          </div>
        </div>
      </form>
    </Form>
  )
}

// Modified CommentActions component that supports inline editing
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
            className="size-6 rounded-md opacity-0 group-hover:opacity-100 data-[state=open]:bg-accent data-[state=open]:text-accent-foreground data-[state=open]:opacity-100"
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
