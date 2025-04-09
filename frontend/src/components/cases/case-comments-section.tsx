"use client"

import type React from "react"
import { useState } from "react"
import { CaseCommentRead } from "@/client"
import { zodResolver } from "@hookform/resolvers/zod"
import { formatDistanceToNow } from "date-fns"
import {
  ArrowUpIcon,
  Clock,
  MoreHorizontal,
  PaperclipIcon,
  PencilIcon,
  Trash2Icon,
} from "lucide-react"
import { useForm } from "react-hook-form"
import { z } from "zod"

import { getDisplayName, SYSTEM_USER } from "@/lib/auth"
import {
  useCaseComments,
  useCreateCaseComment,
  useDeleteCaseComment,
  useUpdateCaseComment,
} from "@/lib/hooks"
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
import { Avatar, AvatarFallback } from "@/components/ui/avatar"
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
import {
  HoverCard,
  HoverCardContent,
  HoverCardTrigger,
} from "@/components/ui/hover-card"
import { Separator } from "@/components/ui/separator"
import { Skeleton } from "@/components/ui/skeleton"
import { Textarea } from "@/components/ui/textarea"
import {
  Tooltip,
  TooltipContent,
  TooltipProvider,
  TooltipTrigger,
} from "@/components/ui/tooltip"
import { toast } from "@/components/ui/use-toast"

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
      <>
        <CommentSkeleton />
        <CommentSkeleton />
        <CommentSkeleton />
      </>
    )
  }
  return (
    <div className="max-w- mx-auto w-full">
      <Separator />
      <div className="space-y-4 p-4">
        {caseComments?.map((comment) => {
          const user = comment.user ?? SYSTEM_USER
          const displayName = getDisplayName(user)
          const username = user.email.split("@")[0]
          const isEditing = editingCommentId === comment.id

          return (
            <div key={comment.id} className="group flex gap-3">
              <HoverCard>
                <HoverCardTrigger asChild>
                  <Avatar className="size-8 cursor-default">
                    <AvatarFallback className="bg-primary/10 text-xs text-primary">
                      {displayName.substring(0, 2).toUpperCase()}
                    </AvatarFallback>
                  </Avatar>
                </HoverCardTrigger>
                <HoverCardContent className="w-auto" side="top">
                  <div className="flex items-center gap-4">
                    <Avatar className="size-16">
                      <AvatarFallback className="bg-primary/10 text-lg text-primary">
                        {displayName.substring(0, 2).toUpperCase()}
                      </AvatarFallback>
                    </Avatar>
                    <div className="flex flex-col gap-1">
                      <div className="flex items-center gap-2">
                        <span className="text-base font-medium">
                          {displayName}
                        </span>
                        <span className="text-muted-foreground">
                          ({username})
                        </span>
                        {user.role && (
                          <span className="rounded-md bg-primary/10 px-2 py-0.5 text-xs font-medium capitalize text-primary">
                            {user.role}
                          </span>
                        )}
                      </div>
                      <span className="text-xs text-muted-foreground">
                        {user.email}
                      </span>
                    </div>
                  </div>
                </HoverCardContent>
              </HoverCard>
              <div className="flex-1 space-y-1">
                <div className="flex items-center justify-between gap-2">
                  <div className="flex items-center gap-2">
                    {/* This should be user name */}
                    <span className="text-sm font-medium">{displayName}</span>
                    <TooltipProvider>
                      <Tooltip>
                        <TooltipTrigger className="cursor-default">
                          <span className="flex items-center gap-1 text-xs text-muted-foreground">
                            <Clock className="size-3" />
                            {formatDistanceToNow(new Date(comment.created_at), {
                              addSuffix: true,
                            })}
                            {comment.last_edited_at && (
                              <span className="ml-1">(edited)</span>
                            )}
                          </span>
                        </TooltipTrigger>
                        <TooltipContent>
                          Created:{" "}
                          {new Date(comment.created_at).toLocaleString()}
                          {comment.last_edited_at && (
                            <>
                              <br />
                              Edited:{" "}
                              {new Date(
                                comment.last_edited_at
                              ).toLocaleString()}
                            </>
                          )}
                        </TooltipContent>
                      </Tooltip>
                    </TooltipProvider>
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

                {isEditing ? (
                  <InlineCommentEdit
                    comment={comment}
                    caseId={caseId}
                    workspaceId={workspaceId}
                    onStopEditing={() => setEditingCommentId(null)}
                  />
                ) : (
                  <p className="text-sm">{comment.content}</p>
                )}
              </div>
            </div>
          )
        })}
      </div>

      <div className="p-4 pt-0">
        <CommentTextBox caseId={caseId} workspaceId={workspaceId} />
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
  const form = useForm<CommentFormSchema>({
    resolver: zodResolver(commentFormSchema),
    defaultValues: {
      content: "",
    },
    mode: "onSubmit",
  })

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
      <div className="relative flex w-full">
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
                      placeholder="Leave a comment..."
                      className="min-h-[80px] w-full resize-none rounded-md border-gray-200 bg-gray-50 pr-16 text-gray-800 placeholder:text-gray-400 focus-visible:ring-muted-foreground/30"
                      value={field.value}
                      onChange={field.onChange}
                      onKeyDown={handleKeyDown}
                    />
                  </FormControl>
                </FormItem>
              )}
            />
            <div className="absolute bottom-3 right-3 flex gap-2">
              <Button
                variant="ghost"
                size="icon"
                className="size-8 rounded-md text-gray-400 hover:bg-gray-100 hover:text-muted-foreground"
                disabled
              >
                <PaperclipIcon className="size-4" />
                <span className="sr-only">Attach file</span>
              </Button>
              <Button
                variant="ghost"
                size="icon"
                type="submit"
                className="size-8 rounded-md text-gray-400 hover:bg-gray-200/80 hover:text-muted-foreground"
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

  const { updateComment } = useUpdateCaseComment({
    caseId,
    workspaceId,
    commentId: comment.id,
  })

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
      <form
        onSubmit={form.handleSubmit(handleSubmit)}
        className="mt-2 space-y-3"
      >
        <FormField
          control={form.control}
          name="content"
          render={({ field }) => (
            <FormItem>
              <FormControl>
                <Textarea
                  autoFocus
                  className="min-h-[80px] w-full resize-none rounded-md border-gray-200 bg-background"
                  onKeyDown={handleKeyDown}
                  {...field}
                />
              </FormControl>
              <FormMessage />
            </FormItem>
          )}
        />
        <div className="flex justify-end gap-2">
          <Button
            type="button"
            variant="ghost"
            size="sm"
            onClick={onStopEditing}
          >
            Cancel
          </Button>
          <Button type="submit" size="sm">
            Save
          </Button>
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
