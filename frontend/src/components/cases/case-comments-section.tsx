"use client"

import { zodResolver } from "@hookform/resolvers/zod"
import { useQuery } from "@tanstack/react-query"
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
import { useCallback, useLayoutEffect, useMemo, useRef, useState } from "react"
import { useForm } from "react-hook-form"
import { z } from "zod"
import {
  type CaseCommentRead,
  type CaseCommentThreadRead,
  foldersListFolders,
  type WorkflowFolderRead,
  type WorkflowReadMinimal,
  workflowsListWorkflows,
} from "@/client"
import {
  ModelSelector,
  ModelSelectorContent,
  ModelSelectorEmpty,
  ModelSelectorGroup,
  ModelSelectorInput,
  ModelSelectorItem,
  ModelSelectorList,
  ModelSelectorTrigger,
} from "@/components/ai-elements/model-selector"
import { CaseCommentViewer } from "@/components/cases/case-description-editor"
import {
  CaseEventTimestamp,
  CaseUserAvatar,
} from "@/components/cases/case-panel-common"
import { TagBadge } from "@/components/tag-badge"
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
import {
  HoverCard,
  HoverCardContent,
  HoverCardTrigger,
} from "@/components/ui/hover-card"
import { ScrollArea } from "@/components/ui/scroll-area"
import { Skeleton } from "@/components/ui/skeleton"
import { Textarea } from "@/components/ui/textarea"
import { toast } from "@/components/ui/use-toast"
import { useAuth } from "@/hooks/use-auth"
import { useEntitlements } from "@/hooks/use-entitlements"
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
import { cn } from "@/lib/utils"

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

type WorkflowCommentSelectorItem = {
  id: string
  title: string
  alias: string | null
  folderName: string
  folderPath: string | null
  showFolderPath: boolean
  tags: WorkflowReadMinimal["tags"]
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

function toWorkflowCommentSelectorItems(
  workflows: WorkflowReadMinimal[],
  folders: WorkflowFolderRead[]
): WorkflowCommentSelectorItem[] {
  const folderMap = new Map(folders.map((folder) => [folder.id, folder]))
  const folderNameCounts = folders.reduce(
    (counts, folder) =>
      counts.set(folder.name, (counts.get(folder.name) ?? 0) + 1),
    new Map<string, number>()
  )

  return workflows.map((workflow) => {
    const folder = workflow.folder_id ? folderMap.get(workflow.folder_id) : null
    const folderName = folder?.name ?? "No folder"
    return {
      id: workflow.id,
      title: workflow.title,
      alias: workflow.alias ?? null,
      folderName,
      folderPath: folder?.path ?? null,
      showFolderPath: folder
        ? (folderNameCounts.get(folder.name) ?? 0) > 1
        : false,
      tags: workflow.tags,
    }
  })
}

function useCommentWorkflowSelectorData(
  workspaceId: string,
  enabled: boolean
): {
  items: WorkflowCommentSelectorItem[]
  isLoading: boolean
} {
  const { data: workflows = [], isLoading: workflowsIsLoading } = useQuery({
    queryKey: ["comment-workflows", workspaceId],
    queryFn: async () => {
      const response = await workflowsListWorkflows({
        workspaceId,
        limit: 0,
      })
      return response.items
    },
    enabled,
    staleTime: 5 * 60 * 1000,
  })
  const { data: folders = [], isLoading: foldersIsLoading } = useQuery({
    queryKey: ["comment-workflow-folders", workspaceId],
    queryFn: async () => await foldersListFolders({ workspaceId }),
    enabled,
    staleTime: 5 * 60 * 1000,
  })

  const items = useMemo(
    () => toWorkflowCommentSelectorItems(workflows, folders),
    [folders, workflows]
  )

  return {
    items,
    isLoading: workflowsIsLoading || foldersIsLoading,
  }
}

function WorkflowSelectorTagBadges({
  tags,
}: {
  tags: WorkflowReadMinimal["tags"]
}) {
  if (!tags?.length) {
    return null
  }

  const [firstTag, ...remainingTags] = tags
  if (!firstTag) {
    return null
  }

  return (
    <div className="ml-auto flex shrink-0 items-center gap-1">
      <TagBadge tag={firstTag} className="h-5 shrink-0 px-1.5" />
      {remainingTags.length ? (
        <HoverCard openDelay={100} closeDelay={100}>
          <HoverCardTrigger asChild>
            <Badge
              variant="outline"
              className="h-5 shrink-0 rounded-full px-2 text-[11px]"
            >
              + {remainingTags.length}
            </Badge>
          </HoverCardTrigger>
          <HoverCardContent
            side="top"
            align="end"
            className="w-auto max-w-64 px-3 py-2"
          >
            <div className="flex flex-wrap gap-1">
              {tags.map((tag) => (
                <TagBadge key={tag.id} tag={tag} className="h-5 px-1.5" />
              ))}
            </div>
          </HoverCardContent>
        </HoverCard>
      ) : null}
    </div>
  )
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
    <section className="overflow-hidden rounded-lg border border-border/60 px-5 py-4">
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
  const { execution } = useCompactWorkflowExecution(
    comment.workflow?.wf_exec_id ?? undefined
  )
  const workflowStatus = execution
    ? execution.status === "COMPLETED"
      ? "succeeded"
      : execution.status === "RUNNING"
        ? "running"
        : "failed"
    : getWorkflowCommentStatus(comment)
  const workflowRunPath = getWorkflowRunPath(
    workspaceId,
    execution ? execution.id : null
  )
  const canManage = !comment.is_deleted && currentUserId === comment.user?.id

  return (
    <div className="group space-y-3">
      <div className="flex items-start gap-3">
        <div className="min-w-0 flex-1">
          {comment.is_deleted ? null : isWorkflowComment && comment.workflow ? (
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

      {isEditing ? (
        <InlineCommentEdit
          comment={comment}
          caseId={caseId}
          workspaceId={workspaceId}
          onStopEditing={onStopEditing}
        />
      ) : comment.is_deleted ? (
        <p className="text-sm italic text-muted-foreground">Comment deleted</p>
      ) : (
        <ScrollArea className="w-full">
          <div className="min-w-0 text-sm leading-6">
            <CaseCommentViewer content={comment.content} />
          </div>
        </ScrollArea>
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
  const [selectorOpen, setSelectorOpen] = useState(false)
  const [selectedWorkflowId, setSelectedWorkflowId] = useState<string | null>(
    null
  )
  const textareaRef = useRef<HTMLTextAreaElement | null>(null)
  const { items: workflowItems, isLoading: workflowsAreLoading } =
    useCommentWorkflowSelectorData(workspaceId, workflowSelectionEnabled)
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
  const trimmedContent = content.trim()
  const selectedWorkflow = useMemo(
    () =>
      selectedWorkflowId
        ? (workflowItems.find((item) => item.id === selectedWorkflowId) ?? null)
        : null,
    [selectedWorkflowId, workflowItems]
  )
  const workflowItemsByFolder = useMemo(() => {
    const groups = new Map<string, WorkflowCommentSelectorItem[]>()
    for (const item of workflowItems) {
      const group = groups.get(item.folderName)
      if (group) {
        group.push(item)
        continue
      }
      groups.set(item.folderName, [item])
    }
    return [...groups.entries()]
  }, [workflowItems])

  useLayoutEffect(() => {
    adjustTextareaHeight()
  }, [adjustTextareaHeight, content])

  const handleSubmit = async (values: CommentFormSchema) => {
    const nextContent = values.content.trim()
    if (!nextContent) {
      return
    }
    try {
      await createComment({
        content: nextContent,
        parent_id: parentId,
        ...(selectedWorkflowId ? { workflow_id: selectedWorkflowId } : {}),
      })
      form.reset({ content: "" })
      setSelectedWorkflowId(null)
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
        <form
          onSubmit={form.handleSubmit(handleSubmit)}
          className="flex flex-col gap-2"
        >
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
                        ? "min-h-9 resize-none border-none px-0 py-1 text-sm shadow-none focus-visible:ring-0"
                        : "min-h-[72px] resize-none border-none px-0 py-0 text-sm shadow-none focus-visible:ring-0"
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

          <div className="flex items-end justify-between gap-2">
            {workflowSelectionEnabled ? (
              <ModelSelector open={selectorOpen} onOpenChange={setSelectorOpen}>
                <ModelSelectorTrigger asChild>
                  <Button
                    type="button"
                    variant="ghost"
                    className={cn(
                      "h-7 max-w-full justify-start gap-1.5 rounded-full border border-border/70 px-2 text-xs font-normal text-muted-foreground hover:text-foreground",
                      isInline && "h-7"
                    )}
                  >
                    <span className="truncate">
                      {selectedWorkflow
                        ? selectedWorkflow.title
                        : "No workflow selected"}
                    </span>
                    {selectedWorkflow?.alias ? (
                      <Badge
                        variant="outline"
                        className="h-5 shrink-0 rounded-full px-1.5 text-xs leading-none"
                      >
                        {selectedWorkflow.alias}
                      </Badge>
                    ) : null}
                    <ChevronDown className="size-3.5 text-muted-foreground" />
                  </Button>
                </ModelSelectorTrigger>
                <ModelSelectorContent className="max-w-2xl">
                  <ModelSelectorInput placeholder="Search workflows, folders, or tags..." />
                  <ModelSelectorList>
                    <ModelSelectorEmpty>
                      {workflowsAreLoading
                        ? "Loading workflows..."
                        : "No workflows found."}
                    </ModelSelectorEmpty>
                    <ModelSelectorGroup heading="Selection">
                      <ModelSelectorItem
                        value="no workflow selected"
                        onSelect={() => {
                          setSelectedWorkflowId(null)
                          setSelectorOpen(false)
                        }}
                      >
                        <span className="text-sm">No workflow selected</span>
                      </ModelSelectorItem>
                    </ModelSelectorGroup>
                    {workflowItemsByFolder.map(([folderName, items]) => (
                      <ModelSelectorGroup key={folderName} heading={folderName}>
                        {items.map((item) => (
                          <ModelSelectorItem
                            key={item.id}
                            value={[
                              item.title,
                              item.alias ?? "",
                              item.folderName,
                              item.folderPath ?? "",
                              ...(item.tags?.map((tag) => tag.name) ?? []),
                            ].join(" ")}
                            onSelect={() => {
                              setSelectedWorkflowId(item.id)
                              setSelectorOpen(false)
                            }}
                          >
                            <div className="min-w-0 space-y-1 py-1">
                              <div className="flex min-w-0 items-center gap-2 overflow-hidden">
                                <span className="truncate text-sm font-medium">
                                  {item.title}
                                </span>
                                {item.alias ? (
                                  <Badge
                                    variant="outline"
                                    className="h-5 shrink-0 rounded-full px-1.5 text-xs leading-none"
                                  >
                                    {item.alias}
                                  </Badge>
                                ) : null}
                                <WorkflowSelectorTagBadges tags={item.tags} />
                              </div>
                              {item.showFolderPath && item.folderPath ? (
                                <p className="truncate text-xs text-muted-foreground">
                                  {item.folderPath}
                                </p>
                              ) : null}
                            </div>
                          </ModelSelectorItem>
                        ))}
                      </ModelSelectorGroup>
                    ))}
                  </ModelSelectorList>
                </ModelSelectorContent>
              </ModelSelector>
            ) : (
              <div />
            )}
            <Button
              type="submit"
              variant="outline"
              size="icon"
              className="size-7 shrink-0 rounded-full border-border/70"
              disabled={createCommentIsPending || !trimmedContent}
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
