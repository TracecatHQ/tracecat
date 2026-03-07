"use client"

import { zodResolver } from "@hookform/resolvers/zod"
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query"
import {
  FolderIcon,
  GitBranchIcon,
  GitPullRequestIcon,
  WorkflowIcon,
} from "lucide-react"
import Link from "next/link"
import { useEffect, useMemo, useState } from "react"
import { useForm } from "react-hook-form"
import { z } from "zod"
import {
  type ApiError,
  type GitBranchInfo,
  type WorkflowBulkPushExcludedWorkflow,
  type WorkflowBulkPushPreviewResponse,
  workflowsBulkPushWorkflows,
  workflowsListWorkflowBranches,
  workflowsPreviewBulkPushWorkflows,
} from "@/client"
import { Spinner } from "@/components/loading/spinner"
import { Badge } from "@/components/ui/badge"
import { Button } from "@/components/ui/button"
import {
  Dialog,
  DialogContent,
  DialogDescription,
  DialogFooter,
  DialogHeader,
  DialogTitle,
} from "@/components/ui/dialog"
import {
  Form,
  FormControl,
  FormField,
  FormItem,
  FormLabel,
  FormMessage,
} from "@/components/ui/form"
import { Input } from "@/components/ui/input"
import {
  Select,
  SelectContent,
  SelectItem,
  SelectSeparator,
  SelectTrigger,
  SelectValue,
} from "@/components/ui/select"
import { Textarea } from "@/components/ui/textarea"
import { ToastAction } from "@/components/ui/toast"
import { toast } from "@/components/ui/use-toast"
import type { TracecatApiError } from "@/lib/errors"

const CREATE_NEW_BRANCH_VALUE = "__create_new_branch__"

const bulkPushFormSchema = z.object({
  branch: z.string().trim().min(1, "Target branch is required"),
  commitMessage: z.string().trim().min(1, "Commit message is required"),
  prTitle: z.string().trim().min(1, "Pull request title is required"),
  prBody: z.string().trim(),
})

type BulkPushFormValues = z.infer<typeof bulkPushFormSchema>

function getErrorMessage(error: unknown, fallback: string): string {
  const apiError = error as TracecatApiError<unknown>
  const detail =
    typeof apiError === "object" && apiError !== null
      ? apiError.body?.detail
      : undefined
  if (typeof detail === "string" && detail.trim()) {
    return detail
  }
  if (error instanceof Error && error.message.trim()) {
    return error.message
  }
  return fallback
}

function getPushToastTitle(result: {
  status: "committed" | "no_op"
  pr_url?: string | null
  pr_reused?: boolean
}): string {
  if (result.status === "no_op") {
    if (result.pr_url && result.pr_reused) {
      return "No changes (PR reused)"
    }
    if (result.pr_url) {
      return "No changes (PR created)"
    }
    return "No changes to push"
  }

  if (result.pr_url && result.pr_reused) {
    return "Workflows pushed (PR reused)"
  }
  if (result.pr_url) {
    return "Workflows pushed (PR created)"
  }
  return "Workflows pushed"
}

function getDisplayFolderPath(folderPath?: string | null): string {
  if (!folderPath || folderPath === "/") {
    return "/"
  }

  return folderPath.endsWith("/") ? folderPath.slice(0, -1) : folderPath
}

function SelectedWorkflowList({
  workspaceId,
  preview,
}: {
  workspaceId: string
  preview: WorkflowBulkPushPreviewResponse
}) {
  const eligibleWorkflows = preview.eligible_workflows ?? []
  const excludedWorkflows = preview.excluded_workflows ?? []
  const excludedWorkflowGroups = excludedWorkflows.reduce<
    Array<{
      message: string
      workflows: WorkflowBulkPushExcludedWorkflow[]
    }>
  >((groups, workflow) => {
    const message = workflow.message || "Unknown exclusion reason"
    const existingGroup = groups.find((group) => group.message === message)

    if (existingGroup) {
      existingGroup.workflows.push(workflow)
      return groups
    }

    groups.push({ message, workflows: [workflow] })
    return groups
  }, [])

  return (
    <div className="space-y-4">
      <div className="space-y-2">
        <div className="flex items-center gap-2">
          <div className="text-xs font-medium text-foreground">Included</div>
          <span className="text-xs text-muted-foreground">
            {eligibleWorkflows.length}
          </span>
        </div>
        <div className="max-h-44 overflow-auto rounded-md border">
          {eligibleWorkflows.length > 0 ? (
            <div className="divide-y">
              {eligibleWorkflows.map((workflow, index) => {
                const content = (
                  <div className="flex items-center justify-between gap-3 px-3 py-2">
                    <div className="flex min-w-0 items-center gap-2">
                      <div className="flex min-w-0 items-center gap-1.5">
                        <WorkflowIcon className="size-3.5 shrink-0 text-muted-foreground" />
                        <p className="truncate text-sm font-medium">
                          {workflow.title}
                        </p>
                      </div>
                      <span className="inline-flex shrink-0 items-center gap-1 rounded-sm bg-muted px-1.5 py-0.5 text-[11px] text-foreground">
                        <FolderIcon className="size-3 shrink-0 text-muted-foreground" />
                        <span>
                          {getDisplayFolderPath(workflow.folder_path)}
                        </span>
                      </span>
                    </div>
                    <Badge
                      variant="secondary"
                      className="h-5 shrink-0 rounded-sm px-2 text-[10px] font-normal"
                    >
                      v{workflow.latest_definition_version}
                    </Badge>
                  </div>
                )

                return workflow.workflow_id ? (
                  <Link
                    key={workflow.workflow_id}
                    href={`/workspaces/${workspaceId}/workflows/${workflow.workflow_id}`}
                    title={workflow.workflow_id}
                    target="_blank"
                    rel="noreferrer"
                    className="block transition-colors hover:bg-muted/40"
                  >
                    {content}
                  </Link>
                ) : (
                  <div
                    key={`included-${workflow.title ?? "workflow"}-${index}`}
                    title={workflow.workflow_id ?? undefined}
                  >
                    {content}
                  </div>
                )
              })}
            </div>
          ) : (
            <div className="px-3 py-4 text-sm text-muted-foreground">
              No published workflows are eligible to push.
            </div>
          )}
        </div>
      </div>

      <div className="space-y-2">
        <div className="flex items-center gap-2">
          <div className="text-xs font-medium text-foreground">Excluded</div>
          <span className="text-xs text-muted-foreground">
            {excludedWorkflows.length}
          </span>
        </div>
        <div className="max-h-36 overflow-auto rounded-md border">
          {excludedWorkflowGroups.length > 0 ? (
            <div className="divide-y">
              {excludedWorkflowGroups.map((group) => (
                <ExcludedWorkflowGroupRow
                  key={group.message}
                  message={group.message}
                  workspaceId={workspaceId}
                  workflows={group.workflows}
                />
              ))}
            </div>
          ) : (
            <div className="px-3 py-4 text-sm text-muted-foreground">
              Nothing excluded from this push.
            </div>
          )}
        </div>
      </div>
    </div>
  )
}

function ExcludedWorkflowGroupRow({
  message,
  workspaceId,
  workflows,
}: {
  message: string
  workspaceId: string
  workflows: WorkflowBulkPushExcludedWorkflow[]
}) {
  return (
    <div className="space-y-1 px-3 py-2">
      <div className="flex items-start justify-between gap-3">
        <p className="text-xs text-muted-foreground">{message}</p>
        <Badge
          variant="secondary"
          className="h-5 shrink-0 rounded-sm px-2 text-[10px] font-normal"
        >
          {workflows.length}
        </Badge>
      </div>
      <div className="flex flex-wrap gap-1">
        {workflows.map((workflow, index) => {
          const label =
            workflow.title || workflow.workflow_id || "Unknown workflow"
          const workflowId = workflow.workflow_id

          if (workflowId) {
            return (
              <Link
                key={workflowId}
                href={`/workspaces/${workspaceId}/workflows/${workflowId}`}
                title={workflowId}
                target="_blank"
                rel="noreferrer"
                className="inline-flex items-center gap-1 rounded-sm bg-muted px-1.5 py-0.5 text-[11px] text-foreground transition-colors hover:bg-muted/80"
              >
                <WorkflowIcon className="size-3 text-muted-foreground" />
                <span>{label}</span>
              </Link>
            )
          }

          return (
            <span
              key={`excluded-${label}-${index}`}
              className="inline-flex items-center gap-1 rounded-sm bg-muted px-1.5 py-0.5 text-[11px] text-foreground"
            >
              <WorkflowIcon className="size-3 text-muted-foreground" />
              <span>{label}</span>
            </span>
          )
        })}
      </div>
    </div>
  )
}

export function WorkflowBulkPushDialog({
  open,
  onOpenChange,
  workspaceId,
  selectedWorkflowIds,
  selectedFolderPaths,
  onSuccess,
}: {
  open: boolean
  onOpenChange: (open: boolean) => void
  workspaceId: string
  selectedWorkflowIds: string[]
  selectedFolderPaths: string[]
  onSuccess?: () => void
}) {
  const queryClient = useQueryClient()
  const [isCreatingBranch, setIsCreatingBranch] = useState(false)

  const sortedWorkflowIds = useMemo(
    () => [...selectedWorkflowIds].sort(),
    [selectedWorkflowIds]
  )
  const sortedFolderPaths = useMemo(
    () => [...selectedFolderPaths].sort(),
    [selectedFolderPaths]
  )
  const hasSelection =
    sortedWorkflowIds.length > 0 || sortedFolderPaths.length > 0

  const form = useForm<BulkPushFormValues>({
    resolver: zodResolver(bulkPushFormSchema),
    defaultValues: {
      branch: "",
      commitMessage: "",
      prTitle: "",
      prBody: "",
    },
  })

  const previewQuery = useQuery<WorkflowBulkPushPreviewResponse, ApiError>({
    queryKey: [
      "workflow-bulk-push-preview",
      workspaceId,
      sortedWorkflowIds,
      sortedFolderPaths,
    ],
    queryFn: async () =>
      await workflowsPreviewBulkPushWorkflows({
        workspaceId,
        requestBody: {
          workflow_ids: sortedWorkflowIds,
          folder_paths: sortedFolderPaths,
        },
      }),
    enabled: open && hasSelection,
    refetchOnWindowFocus: false,
  })

  const {
    data: repoBranches,
    isLoading: branchesLoading,
    error: branchesError,
  } = useQuery<Array<GitBranchInfo>, ApiError>({
    queryKey: ["workflow-sync-branches", workspaceId],
    queryFn: async () =>
      await workflowsListWorkflowBranches({
        workspaceId,
        limit: 200,
      }),
    enabled: open,
    refetchOnWindowFocus: false,
  })

  const hasBranches = (repoBranches?.length ?? 0) > 0

  useEffect(() => {
    if (!open || !previewQuery.data) {
      return
    }

    form.reset({
      branch: previewQuery.data.branch,
      commitMessage: previewQuery.data.commit_message,
      prTitle: previewQuery.data.pr_title,
      prBody: previewQuery.data.pr_body,
    })
  }, [form, open, previewQuery.data])

  const selectedBranch = form.watch("branch")

  useEffect(() => {
    if (!open || !repoBranches || repoBranches.length === 0) {
      return
    }

    const branchNames = new Set(repoBranches.map((branch) => branch.name))
    setIsCreatingBranch(!selectedBranch || !branchNames.has(selectedBranch))
  }, [open, repoBranches, selectedBranch])

  const pushMutation = useMutation({
    mutationFn: async (values: BulkPushFormValues) => {
      if (!previewQuery.data) {
        throw new Error("Bulk push preview has not loaded.")
      }
      return await workflowsBulkPushWorkflows({
        workspaceId,
        requestBody: {
          workflow_ids: previewQuery.data.resolved_workflow_ids ?? [],
          branch: values.branch,
          commit_message: values.commitMessage,
          pr_title: values.prTitle,
          pr_body: values.prBody || undefined,
        },
      })
    },
    onSuccess: (result) => {
      toast({
        title: getPushToastTitle(result),
        description: result.message,
        action: result.pr_url ? (
          <ToastAction
            altText="Open pull request"
            onClick={() =>
              window.open(result.pr_url ?? "", "_blank", "noopener,noreferrer")
            }
          >
            View PR
          </ToastAction>
        ) : undefined,
      })
      queryClient.invalidateQueries({ queryKey: ["workflows"] })
      queryClient.invalidateQueries({ queryKey: ["directory-items"] })
      queryClient.invalidateQueries({ queryKey: ["workflow-sync-branches"] })
      onSuccess?.()
      onOpenChange(false)
    },
    onError: (error: unknown) => {
      toast({
        title: "Failed to push workflows",
        description: getErrorMessage(
          error,
          "The workflows could not be pushed to GitHub."
        ),
      })
    },
  })

  const selectedBranchInfo = repoBranches?.find(
    (branch) => branch.name === selectedBranch
  )
  const isSelectedDefaultBranch = selectedBranchInfo?.is_default ?? false
  const previewErrorMessage = previewQuery.error
    ? getErrorMessage(
        previewQuery.error,
        "The bulk push preview could not be loaded."
      )
    : null
  const branchesErrorMessage = branchesError
    ? getErrorMessage(
        branchesError,
        "The GitHub branches could not be loaded for this workspace."
      )
    : null

  const canSubmit =
    hasSelection &&
    previewQuery.isSuccess &&
    (previewQuery.data.can_submit ?? false) &&
    hasBranches &&
    !branchesLoading &&
    !isSelectedDefaultBranch &&
    !pushMutation.isPending

  return (
    <Dialog open={open} onOpenChange={onOpenChange}>
      <DialogContent className="flex max-h-[90vh] flex-col overflow-hidden sm:max-w-[760px]">
        <DialogHeader>
          <DialogTitle className="flex items-center gap-2">
            <GitBranchIcon className="size-4" />
            Push to GitHub
          </DialogTitle>
          <DialogDescription>
            Push the latest published definitions for the selected workflows to
            one branch and one pull request.
          </DialogDescription>
        </DialogHeader>

        {!hasSelection ? (
          <div className="rounded-md border px-3 py-4 text-sm text-muted-foreground">
            Select at least one workflow or folder to continue.
          </div>
        ) : previewQuery.isLoading ? (
          <div className="flex min-h-[280px] items-center justify-center">
            <div className="flex items-center gap-2 text-sm text-muted-foreground">
              <Spinner className="size-4" segmentColor="currentColor" />
              Loading preview...
            </div>
          </div>
        ) : previewErrorMessage ? (
          <div className="rounded-md border border-destructive/30 bg-destructive/5 px-3 py-4 text-sm text-destructive">
            {previewErrorMessage}
          </div>
        ) : previewQuery.data ? (
          <div className="min-h-0 flex-1 overflow-y-auto pr-1">
            <Form {...form}>
              <form
                onSubmit={form.handleSubmit((values) =>
                  pushMutation.mutateAsync(values)
                )}
                className="flex min-h-0 flex-col"
              >
                <div className="space-y-5">
                  <SelectedWorkflowList
                    workspaceId={workspaceId}
                    preview={previewQuery.data}
                  />

                  <div className="grid gap-4 md:grid-cols-2">
                    <FormField
                      control={form.control}
                      name="branch"
                      render={({ field }) => (
                        <FormItem className="md:col-span-2">
                          <FormLabel>Target branch</FormLabel>
                          <Select
                            value={
                              isCreatingBranch ||
                              !repoBranches?.some(
                                (branch) => branch.name === field.value
                              )
                                ? CREATE_NEW_BRANCH_VALUE
                                : field.value
                            }
                            onValueChange={(value) => {
                              if (value === CREATE_NEW_BRANCH_VALUE) {
                                setIsCreatingBranch(true)
                                field.onChange("")
                                return
                              }
                              setIsCreatingBranch(false)
                              field.onChange(value)
                            }}
                            disabled={branchesLoading || !hasBranches}
                          >
                            <FormControl>
                              <SelectTrigger className="h-9">
                                <SelectValue placeholder="Select branch" />
                              </SelectTrigger>
                            </FormControl>
                            <SelectContent>
                              {hasBranches ? (
                                <>
                                  <SelectItem value={CREATE_NEW_BRANCH_VALUE}>
                                    Create new branch...
                                  </SelectItem>
                                  <SelectSeparator />
                                  {(repoBranches ?? []).map((branch) => (
                                    <SelectItem
                                      key={branch.name}
                                      value={branch.name}
                                      disabled={branch.is_default}
                                    >
                                      <div className="flex items-center gap-2">
                                        <span>{branch.name}</span>
                                        {branch.is_default && (
                                          <Badge
                                            variant="secondary"
                                            className="h-4 rounded-sm px-1 text-[10px] font-normal"
                                          >
                                            default
                                          </Badge>
                                        )}
                                      </div>
                                    </SelectItem>
                                  ))}
                                </>
                              ) : (
                                <SelectItem value="__no_branches" disabled>
                                  No branches found
                                </SelectItem>
                              )}
                            </SelectContent>
                          </Select>
                          {isCreatingBranch ? (
                            <div className="mt-2">
                              <Input
                                value={field.value}
                                onChange={field.onChange}
                                placeholder="tracecat/bulk-push"
                              />
                              <p className="mt-1 text-xs text-muted-foreground">
                                The branch will be created from the repository
                                default branch if it does not exist.
                              </p>
                            </div>
                          ) : null}
                          {branchesErrorMessage ? (
                            <p className="text-xs text-destructive">
                              {branchesErrorMessage}
                            </p>
                          ) : null}
                          {!branchesLoading && !hasBranches ? (
                            <p className="text-xs text-muted-foreground">
                              No branches are available from the configured
                              repository.
                            </p>
                          ) : null}
                          {isSelectedDefaultBranch ? (
                            <p className="text-xs text-destructive">
                              Bulk pushes always open a pull request, so select
                              or create a branch other than the default branch.
                            </p>
                          ) : null}
                          <FormMessage />
                        </FormItem>
                      )}
                    />

                    <FormField
                      control={form.control}
                      name="commitMessage"
                      render={({ field }) => (
                        <FormItem>
                          <FormLabel>Commit message</FormLabel>
                          <FormControl>
                            <Input {...field} />
                          </FormControl>
                          <FormMessage />
                        </FormItem>
                      )}
                    />

                    <FormField
                      control={form.control}
                      name="prTitle"
                      render={({ field }) => (
                        <FormItem>
                          <FormLabel>Pull request title</FormLabel>
                          <FormControl>
                            <Input {...field} />
                          </FormControl>
                          <FormMessage />
                        </FormItem>
                      )}
                    />
                  </div>

                  <FormField
                    control={form.control}
                    name="prBody"
                    render={({ field }) => (
                      <FormItem>
                        <FormLabel>Pull request description</FormLabel>
                        <FormControl>
                          <Textarea
                            {...field}
                            className="min-h-32 resize-y"
                            placeholder="Describe this workflow push"
                          />
                        </FormControl>
                        <FormMessage />
                      </FormItem>
                    )}
                  />

                  {!previewQuery.data.can_submit ? (
                    <div className="rounded-md border border-amber-200 bg-amber-50 px-3 py-2 text-sm text-amber-800">
                      Nothing eligible to push. Only published workflows can be
                      included in the pull request.
                    </div>
                  ) : null}
                </div>

                <DialogFooter className="mt-5 gap-2">
                  <Button
                    type="button"
                    variant="outline"
                    onClick={() => onOpenChange(false)}
                    disabled={pushMutation.isPending}
                  >
                    Cancel
                  </Button>
                  <Button type="submit" disabled={!canSubmit}>
                    {pushMutation.isPending ? (
                      <>
                        <Spinner
                          className="mr-2 size-4"
                          segmentColor="currentColor"
                        />
                        Pushing to GitHub...
                      </>
                    ) : (
                      <>
                        <GitPullRequestIcon className="mr-2 size-4" />
                        Push to GitHub
                      </>
                    )}
                  </Button>
                </DialogFooter>
              </form>
            </Form>
          </div>
        ) : null}
      </DialogContent>
    </Dialog>
  )
}
