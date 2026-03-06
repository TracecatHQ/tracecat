"use client"

import { zodResolver } from "@hookform/resolvers/zod"
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query"
import { GitBranchIcon, GitPullRequestIcon } from "lucide-react"
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

function SelectedWorkflowList({
  preview,
}: {
  preview: WorkflowBulkPushPreviewResponse
}) {
  const eligibleWorkflows = preview.eligible_workflows ?? []
  const excludedWorkflows = preview.excluded_workflows ?? []

  return (
    <div className="space-y-4">
      <div className="flex flex-wrap items-center gap-2">
        <Badge variant="secondary" className="h-6 rounded-md px-2 text-xs">
          {eligibleWorkflows.length} eligible
        </Badge>
        <Badge variant="secondary" className="h-6 rounded-md px-2 text-xs">
          {excludedWorkflows.length} excluded
        </Badge>
        <Badge variant="secondary" className="h-6 rounded-md px-2 text-xs">
          1 pull request
        </Badge>
      </div>

      <div className="space-y-2">
        <div className="text-xs font-medium text-foreground">Included</div>
        <div className="max-h-44 overflow-auto rounded-md border">
          {eligibleWorkflows.length > 0 ? (
            <div className="divide-y">
              {eligibleWorkflows.map((workflow) => (
                <div
                  key={workflow.workflow_id}
                  className="flex items-start justify-between gap-3 px-3 py-2"
                >
                  <div className="min-w-0">
                    <p className="truncate text-sm font-medium">
                      {workflow.title}
                    </p>
                    <p className="truncate text-xs text-muted-foreground">
                      {workflow.workflow_id}
                      {workflow.folder_path ? ` • ${workflow.folder_path}` : ""}
                    </p>
                  </div>
                  <Badge
                    variant="secondary"
                    className="h-5 shrink-0 rounded-sm px-2 text-[10px] font-normal"
                  >
                    v{workflow.latest_definition_version}
                  </Badge>
                </div>
              ))}
            </div>
          ) : (
            <div className="px-3 py-4 text-sm text-muted-foreground">
              No published workflows are eligible to push.
            </div>
          )}
        </div>
      </div>

      <div className="space-y-2">
        <div className="text-xs font-medium text-foreground">Excluded</div>
        <div className="max-h-36 overflow-auto rounded-md border">
          {excludedWorkflows.length > 0 ? (
            <div className="divide-y">
              {excludedWorkflows.map((workflow, index) => (
                <ExcludedWorkflowRow
                  key={`${workflow.workflow_id ?? "excluded"}-${index}`}
                  workflow={workflow}
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

function ExcludedWorkflowRow({
  workflow,
}: {
  workflow: WorkflowBulkPushExcludedWorkflow
}) {
  return (
    <div className="px-3 py-2">
      <p className="truncate text-sm font-medium">
        {workflow.title || workflow.workflow_id || "Unknown workflow"}
      </p>
      <p className="text-xs text-muted-foreground">{workflow.message}</p>
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
    setIsCreatingBranch(true)
  }, [form, open, previewQuery.data])

  useEffect(() => {
    if (!open || !repoBranches || repoBranches.length === 0) {
      return
    }

    const branchNames = new Set(repoBranches.map((branch) => branch.name))
    const currentBranch = form.getValues("branch")
    setIsCreatingBranch(!currentBranch || !branchNames.has(currentBranch))
  }, [form, open, repoBranches])

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

  const selectedBranch = form.watch("branch")
  const selectedBranchInfo = repoBranches?.find(
    (branch) => branch.name === selectedBranch
  )
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
    !pushMutation.isPending

  return (
    <Dialog open={open} onOpenChange={onOpenChange}>
      <DialogContent className="max-h-[90vh] overflow-hidden sm:max-w-[760px]">
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
          <div className="min-h-0 overflow-auto pr-1">
            <Form {...form}>
              <form
                onSubmit={form.handleSubmit((values) =>
                  pushMutation.mutateAsync(values)
                )}
                className="space-y-5"
              >
                <SelectedWorkflowList preview={previewQuery.data} />

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
                        {selectedBranchInfo?.is_default ? (
                          <p className="text-xs text-muted-foreground">
                            A pull request will still be created for this bulk
                            push.
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

                <DialogFooter className="gap-2">
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
